#!/usr/bin/env python3

"""
Terraform State Migration Script

This script automates the process of moving Terraform state between two root modules stored in an AWS S3 backend.

### File Structure:
- `move_resources_to_b.txt`: A text file located in the source module folder. Each line in this file should contain:
    - A Terraform resource address (e.g., `aws_instance.example`)
    - A module address (e.g., `module.vpc`), which moves all resources inside the module

### Workflow:
1. Pull the Terraform state for both source and destination modules from AWS S3 and store them locally.
2. Verify that all listed resources exist in the source state file.
3. Move each resource/module from the source state to the destination state.
4. Run `terraform plan` on both local state files to ensure no unexpected changes.
5. Push the updated local state files back to AWS S3.
6. Clean up temporary local state files unless `--keep-local` is specified.

### Assumptions:
- Terraform CLI is installed and configured.
- AWS S3 backend is set up correctly for `terraform state pull/push`.
- The script exits immediately if `terraform state pull/push` or `terraform plan` fails.

### Logging:
- A log file (`migration.log`) is created in each module directory.
- Logs all executed Terraform commands and their output.

Copyright (C) 2025 by Oliver Lars Schoenborn 
"""

import argparse
import subprocess
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from boto3.docs.utils import is_resource_action
from duplicity.config import dry_run

TF_EXIT_CODES = {
    0: 'No changes detected.',
    1: 'Error occurred.',
    2: 'Changes detected!',
}


def log_to_file(module_path: Path, message: str):
    log_file = module_path / 'migration.log'
    with open(log_file, 'a') as log:
        log.write(message + '\n')


def print_command(command: List[str], cwd: Path = None):
    print('[INFO] Will execute:', ' '.join(command))
    if cwd:
        print(f'[INFO]   (in dir: {cwd})')


def run_command(command: List[str], capture_output: bool = False, cwd: Path = None) -> str:
    print_command(command, cwd)
    try:
        result = subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=capture_output)
        return result.stdout.strip() if capture_output else ''
    except subprocess.CalledProcessError as e:
        print(f'[ERROR] Command failed:', file=sys.stderr)
        print(' '.join(command), file=sys.stderr)
        print(e.stderr if e.stderr else e, file=sys.stderr)
        sys.exit(e.returncode)


def check_terraform_installed() -> None:
    print('\n[INFO] Checking that terraform is installed')
    run_command(['terraform', 'version'], capture_output=True)


def check_aws_credentials() -> None:
    print('\n[INFO] Checking AWS creds')
    run_command(['aws', 'sts', 'get-caller-identity'], capture_output=True)


def pull_tfstate_from_s3(module_path: Path, tfstate_filename: str) -> None:
    tfstate_content = run_command(['terraform', f'-chdir={module_path}', 'state', 'pull'], capture_output=True)
    module_path.joinpath(tfstate_filename).write_text(tfstate_content)


def pull_and_backup_tfstate(module_path: Path) -> Tuple[Path, str]:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    module_name = module_path.name
    store_filename = f's3_{module_name}_{timestamp}_live.tfstate'
    pull_tfstate_from_s3(module_path, store_filename)
    store_path = module_path / store_filename
    backup_path = module_path / f's3_{module_name}_{timestamp}_backup.tfstate'
    shutil.copy(store_path, backup_path)
    return store_path, backup_path.name


def push_tfstate_to_s3(module_path: Path, tfstate_filename: str) -> None:
    run_command(['terraform', f'-chdir={module_path}', 'state', 'push', tfstate_filename])


def validate_no_planned_changes(module_path: Path) -> None:
    command = ['terraform', f'-chdir={module_path}', 'plan', '-detailed-exitcode']
    print_command(command, module_path)
    result = subprocess.run(command, text=True, capture_output=True)
    exit_msg = TF_EXIT_CODES.get(result.returncode, 'Unknown exit code')

    if result.returncode != 0:
        error_message = f'[ERROR] Terraform plan: {exit_msg}\n{result.stdout}'
        print(error_message, file=sys.stderr)
        log_to_file(module_path, error_message)
        sys.exit(1)

    print(f'[INFO] Terraform plan for {module_path}: {exit_msg}')


def move_resource_in_tfstate(address: str, src_tfstate_path: Path, dest_tfstate_path: Path, dry_run: bool) -> None:
    # State move MUST be done from outside any root module
    command = [
        'terraform',
        'state',
        'mv',
        '-state',
        str(src_tfstate_path),
        '-state-out',
        str(dest_tfstate_path),
        address,
        address,
    ]
    if dry_run:
        command.insert(3, '-dry-run')
    run_command(command)


def get_all_resource_addresses_remote_tfstate(module_path: Path) -> List[str]:
    command = ['terraform', f'-chdir={module_path}', 'state', 'list']
    return run_command(command, capture_output=True).splitlines()


def get_all_resource_addresses_local_tfstate(tfstate_path: Path) -> List[str]:
    command = ['terraform', 'state', 'list', '-state', str(tfstate_path)]
    return run_command(command, capture_output=True).splitlines()


def resource_address_exists(address: str, existing_resources: set) -> bool:
    if address in existing_resources:
        return True

    if not address.startswith('module.'):
        # Regular resource, directly missing
        return False

    address_prefix = address + '.'
    return any(existing_address.startswith(address_prefix) for existing_address in existing_resources)


def verify_all_resources_exist(module_path: Path, resources_file: Path, dry_run: bool) -> List[str]:
    print(f'\n[INFO] Checking resources in {module_path}...')
    existing_resources = set(get_all_resource_addresses_remote_tfstate(module_path))
    addresses = [line.strip() for line in resources_file.read_text().splitlines() if line.strip()]
    missing = []
    for address in addresses:
        if not resource_address_exists(address, existing_resources):
            missing.append(address)

    if missing:
        print(f'[ERROR] The following resources were not found in the {module_path}:', file=sys.stderr)
        for res in missing:
            print(f'  - {res}', file=sys.stderr)
        sys.exit(1)

    run_mode = 'dry-run' if dry_run else 'real'
    print(f'[INFO] All resources exist in {module_path}. Proceeding with migration ({run_mode}).')
    return addresses


def validate_migration(src_tfstate_path: Path, dest_tfstate_path: Path, addresses: List[str]) -> None:
    """
    Validates that the migrated resources exist in the destination module's state
    and no longer exist in the source module's state.
    """
    print('\n[INFO] Validating state migration...')

    # Get the latest state resources
    src_resources = set(get_all_resource_addresses_local_tfstate(src_tfstate_path))
    dest_resources = set(get_all_resource_addresses_local_tfstate(dest_tfstate_path))

    missing_in_dest = []
    still_in_src = []

    for address in addresses:
        if resource_address_exists(address, src_resources):
            still_in_src.append(address)
        if not resource_address_exists(address, dest_resources):
            missing_in_dest.append(address)

    if still_in_src:
        print('[ERROR] The following resources are still present in the source module:', file=sys.stderr)
        for res in still_in_src:
            print(f'  - {res}', file=sys.stderr)

    if missing_in_dest:
        print('[ERROR] The following resources were not found in the destination module:', file=sys.stderr)
        for res in missing_in_dest:
            print(f'  - {res}', file=sys.stderr)

    if still_in_src or missing_in_dest:
        sys.exit(1)

    print('[SUCCESS] State migration validated: All resources moved successfully.')


def save_live_tfstates_to_s3(
    src_live_tfstate_path: Path,
    src_backup_tfstate_name: str,
    dest_live_tfstate_path: Path,
    destb_backup_tfstate_name: str,
    planned_changes_fail: bool,
) -> None:
    print('\n[INFO] Pushing source live state to S3...')
    src_module_path = src_live_tfstate_path.parent
    src_live_tfstate_filename = src_live_tfstate_path.name
    push_tfstate_to_s3(src_module_path, src_live_tfstate_filename)

    print('[INFO] Running plan on updated source S3 backend state...')
    try:
        validate_no_planned_changes(src_module_path)
        print('[INFO] No planned changes on source from S3...')
    except SystemExit:
        print('[WARNING] Source in S3 has changes after migration => MIGHT be invalid migration')
        if not planned_changes_fail:
            print('[ERROR] Restoring original source state in S3 (destination state in S3 untouched)...')
            push_tfstate_to_s3(src_module_path, src_backup_tfstate_name)
            sys.exit(1)

    print('[INFO] Pushing DESTINATION live state to S3...')
    dest_module_path = dest_live_tfstate_path.parent
    dest_tfstate_filename = dest_live_tfstate_path.name
    push_tfstate_to_s3(dest_module_path, dest_tfstate_filename)

    print('[INFO] Running plan on updated destination S3 backend state...')
    try:
        validate_no_planned_changes(dest_module_path)
        print('[INFO] No planned changes on destination from S3...')
    except SystemExit:
        print('[WARNING] Destination in S3 has changes after migration => MIGHT be invalid migration')
        if not planned_changes_fail:
            print('[ERROR] Restoring both original tfstates in S3...')
            push_tfstate_to_s3(src_module_path, src_backup_tfstate_name)
            # also restore the source!
            push_tfstate_to_s3(src_module_path, src_backup_tfstate_name)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Migrate Terraform tfstate between modules.')
    parser.add_argument('src_module_path', type=Path, help='Path to the source Terraform module.')
    parser.add_argument('dest_module_path', type=Path, help='Path to the destination Terraform module.')
    parser.add_argument('resources_file', type=Path, help='Path to the file containing resources to migrate.')
    parser.add_argument('--dry-run', action='store_true', help='Simulate the move without making changes.')
    parser.add_argument('--keep-local', action='store_true', help='Do not delete the tfstate files copied from s3.')
    parser.add_argument('--permanent', action='store_true', help='Push (non-dry-run) results to S3 backends.')
    parser.add_argument(
        '--planned-changes-fail',
        action='store_true',
        help='Do NOT rollback changes if either source or destination show planned changes after migration.',
    )
    args = parser.parse_args()

    if args.dry_run:
        migration_type = 'dry-run'
        print('\n[INFO] Dry run mode enabled. No changes will be made.')
    elif args.permanent:
        migration_type = 'PERMANENT'
        print('\n[WARNING] Changes will be PERMANENT.')
    else:
        migration_type = 'local'
        print('\n[INFO] Changes will be remain local.')

    check_terraform_installed()
    check_aws_credentials()

    if args.planned_changes_fail:
        print('\n[ADVICE] Will fail if terraform plans are empty after the migration.')
    else:
        print('\n[ADVICE] Will continue even if terraform plans have changes after migration (changes are typical, eg default tags).')
        print('[ADVICE] If you are sure that the plans will be empty after, break and re-run with --planned-changes-fail.')

    print('\n[INFO] All checks done, verify above info to decide whether to continue.')
    user_input = input(f'\n[QUESTION] Continue with the migration (y/N)? ').strip().lower()
    if user_input not in ['y', 'yes']:
        print('Aborted.')
        sys.exit(0)

    src_module_path = args.src_module_path
    dest_module_path = args.dest_module_path

    addresses = verify_all_resources_exist(src_module_path, args.resources_file, args.dry_run)

    print('\n[INFO] Creating local and backup tfstates...')
    src_live_tfstate_path, src_backup_filename = pull_and_backup_tfstate(src_module_path)
    dest_live_tfstate_path, dest_backup_filename = pull_and_backup_tfstate(dest_module_path)

    print('\n[INFO] Moving resources...')
    for address in addresses:
        print(f'  - Moving {address} ({migration_type})')
        move_resource_in_tfstate(address, src_live_tfstate_path, dest_live_tfstate_path, args.dry_run)

    if not args.dry_run:
        validate_migration(src_live_tfstate_path, dest_live_tfstate_path, addresses)
        if args.permanent:
            save_live_tfstates_to_s3(
                src_live_tfstate_path,
                src_backup_filename,
                dest_live_tfstate_path,
                dest_backup_filename,
                args.planned_changes_fail,
            )
        else:
            print('\n[INFO] re-run with --permanent to save changes to s3')

    # cleanup
    if not args.keep_local:
        print('\n[INFO] Removing local tfstate files fetches from s3...')
        for file in [
            src_live_tfstate_path,
            dest_live_tfstate_path,
            src_live_tfstate_path.with_name(src_backup_filename),
            dest_live_tfstate_path.with_name(dest_backup_filename),
        ]:
            if file.exists():
                file.unlink()

    print(f'\n[SUCCESS] State ({migration_type}) migration completed.')


if __name__ == '__main__':
    main()
