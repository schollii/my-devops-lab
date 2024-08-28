#!/usr/bin/env python3

"""
This script reconstructs the original `docker run` command used to start a given container.

The script works by inspecting both the container and its base image to accurately determine the 
command-line arguments that were used to run the container. 

The script infers the following `docker run` options:

- Environment variables (`-e`)
- Volume mounts (`-v`)
- Port bindings (`-p`)
- Network mode (`--network`)
- Restart policy (`--restart`)
- Logging driver and options (`--log-driver`, `--log-opt`)
- Container name (`--name`)
- Extra hosts (`--add-host`)
- Privileged mode (`--privileged`)
- Read-only root filesystem (`--read-only`)
- User and working directory (`-u`, `-w`)
- Labels (`--label`)
- Devices (`--device`)
- Capabilities (`--cap-add`, `--cap-drop`)
- Exposed ports (`--expose`)
- DNS servers and search domains (`--dns`, `--dns-search`)
- CPU and memory limits (`--cpus`, `--memory`)
- Environment files (`--env-file`)
- Entrypoint overrides (`--entrypoint`)
- Command overrides

The reconstructed command is formatted for readability and so it can be copy-pasted into the terminal. 

Usage:
    python get_docker_run_cmd.py <container_id_or_name>

Arguments:
    <container_id_or_name> : The ID or name of the Docker container to inspect.

Example Output:
    docker run \
      -d \
      -e "MY_ENV_VAR=value" \
      -v "/host/path:/container/path" \
      -p 8080:80 \
      --network my_network \
      --restart=on-failure:5 \
      --log-driver=syslog \
      --log-opt syslog-address=udp://syslog-server:514 \
      --log-opt tag=my_app \
      --name my_container \
      --entrypoint "/custom/entrypoint.sh" \
      custom_command --arg1 --arg2

Note:
    The script requires Docker to be installed and accessible from the command line.
    If the image used by the container is not available locally, it must be pulled for the script 
    to work correctly.

Author: 
    Oliver Schoenborn

Copyright:
    © 2024 Oliver Schoenborn. Permission is hereby granted to use, modify, and distribute this script under the 
    following conditions:

    1. You must give appropriate credit, provide a link to the original source, and indicate if changes were made. 
       You may do so in any reasonable manner, but not in any way that suggests the original author endorses you or your use.
    
    2. This header and the entire docstring must not be removed or altered in any distribution of the script.
    
    3. If you modify the script and distribute it, you must include a clear notice stating that the script has been modified, 
       credit the original author, and provide a link to the original source.
"""

import json
import subprocess
import sys


def run_command(command):
    try:
        result = subprocess.check_output(command, stderr=subprocess.STDOUT)
        return result.decode('utf-8')
    except subprocess.CalledProcessError as e:
        print(f"Error running command {' '.join(command)}:\n{e.output.decode('utf-8')}")
        sys.exit(1)


def get_container_inspect(container_id):
    inspect_result = run_command(['docker', 'inspect', container_id])
    return json.loads(inspect_result)[0]


def get_image_inspect(image_name):
    inspect_result = run_command(['docker', 'inspect', image_name])
    return json.loads(inspect_result)[0]


def safe_get(d, key, default):
    value = d.get(key, default)
    return value if value is not None else default


def reconstruct_docker_run_command(container_id):
    inspect_data = get_container_inspect(container_id)

    cmd_parts = ['docker run']

    # Handle detached mode if the container was run in detached mode
    if (
        safe_get(inspect_data['Config'], 'AttachStdout', False) == False
        and safe_get(inspect_data['Config'], 'AttachStderr', False) == False
    ):
        cmd_parts.append('-d')

    # Environment variables
    env_vars = safe_get(inspect_data['Config'], 'Env', [])
    for env in env_vars:
        cmd_parts.append(f'-e "{env}"')

    # Volumes
    volumes = safe_get(inspect_data['HostConfig'], 'Binds', [])
    for volume in volumes:
        cmd_parts.append(f'-v "{volume}"')

    # Port bindings
    port_bindings = safe_get(inspect_data['HostConfig'], 'PortBindings', {})
    for container_port, host_bindings in port_bindings.items():
        for binding in host_bindings:
            host_ip = binding.get('HostIp', '')
            host_port = binding.get('HostPort', '')
            port_mapping = f"{host_ip + ':' if host_ip else ''}{host_port}:{container_port}"
            cmd_parts.append(f'-p {port_mapping}')

    # Network mode
    network_mode = safe_get(inspect_data['HostConfig'], 'NetworkMode', 'default')
    if network_mode != 'default':
        cmd_parts.append(f'--network {network_mode}')

    # Restart policy
    restart_policy = safe_get(inspect_data['HostConfig']['RestartPolicy'], 'Name', '')
    restart_max = safe_get(inspect_data['HostConfig']['RestartPolicy'], 'MaximumRetryCount', 0)
    if restart_policy:
        if restart_policy == 'on-failure' and restart_max > 0:
            cmd_parts.append(f'--restart={restart_policy}:{restart_max}')
        else:
            cmd_parts.append(f'--restart={restart_policy}')

    # Logging driver
    log_driver = safe_get(inspect_data['HostConfig']['LogConfig'], 'Type', 'json-file')
    if log_driver != 'json-file':
        cmd_parts.append(f'--log-driver={log_driver}')

    # Logging options
    log_opts = safe_get(inspect_data['HostConfig']['LogConfig'], 'Config', {})
    for opt_key, opt_value in log_opts.items():
        cmd_parts.append(f'--log-opt {opt_key}={opt_value}')

    # Container name
    container_name = inspect_data.get('Name', '').lstrip('/')
    if container_name:
        cmd_parts.append(f'--name {container_name}')

    # Extra hosts
    extra_hosts = safe_get(inspect_data['HostConfig'], 'ExtraHosts', [])
    for host in extra_hosts:
        cmd_parts.append(f'--add-host {host}')

    # Privileged
    if safe_get(inspect_data['HostConfig'], 'Privileged', False):
        cmd_parts.append('--privileged')

    # Read-only
    if safe_get(inspect_data['HostConfig'], 'ReadonlyRootfs', False):
        cmd_parts.append('--read-only')

    # User
    user = safe_get(inspect_data['Config'], 'User', '')
    if user:
        cmd_parts.append(f'-u {user}')

    # Working directory
    workdir = safe_get(inspect_data['Config'], 'WorkingDir', '')
    if workdir:
        cmd_parts.append(f'-w "{workdir}"')

    # Labels
    labels = safe_get(inspect_data['Config'], 'Labels', {})
    for key, value in labels.items():
        cmd_parts.append(f'--label "{key}={value}"')

    # Devices
    devices = safe_get(inspect_data['HostConfig'], 'Devices', [])
    for device in devices:
        device_string = device.get('PathOnHost', '')
        if device_string:
            cmd_parts.append(f'--device {device_string}')

    # Cap Add
    cap_add = safe_get(inspect_data['HostConfig'], 'CapAdd', [])
    for cap in cap_add:
        cmd_parts.append(f'--cap-add {cap}')

    # Cap Drop
    cap_drop = safe_get(inspect_data['HostConfig'], 'CapDrop', [])
    for cap in cap_drop:
        cmd_parts.append(f'--cap-drop {cap}')

    # Expose ports
    exposed_ports = safe_get(inspect_data['Config'], 'ExposedPorts', {})
    for port in exposed_ports.keys():
        cmd_parts.append(f'--expose {port}')

    # DNS
    dns_servers = safe_get(inspect_data['HostConfig'], 'Dns', [])
    for dns in dns_servers:
        cmd_parts.append(f'--dns {dns}')

    # DNS Search
    dns_search = safe_get(inspect_data['HostConfig'], 'DnsSearch', [])
    for search in dns_search:
        cmd_parts.append(f'--dns-search {search}')

    # CPU and Memory limits
    cpus = safe_get(inspect_data['HostConfig'], 'NanoCpus', 0) / 1e9
    if cpus:
        cmd_parts.append(f'--cpus {cpus}')
    memory = safe_get(inspect_data['HostConfig'], 'Memory', 0)
    if memory:
        cmd_parts.append(f'--memory {memory}')

    # Environment files
    env_files = safe_get(inspect_data['HostConfig'], 'EnvFile', [])
    for env_file in env_files:
        cmd_parts.append(f'--env-file {env_file}')

    # Image
    image = safe_get(inspect_data['Config'], 'Image', '')
    cmd_parts.append(image)

    # Entrypoint and Command
    # Inspect the image to get default Entrypoint and Cmd
    image_inspect_data = get_image_inspect(image)

    image_entrypoint = safe_get(image_inspect_data['Config'], 'Entrypoint', [])
    image_cmd = safe_get(image_inspect_data['Config'], 'Cmd', [])

    container_entrypoint = safe_get(inspect_data['Config'], 'Entrypoint', [])
    container_cmd = safe_get(inspect_data['Config'], 'Cmd', [])

    # Compare and add --entrypoint if overridden
    if container_entrypoint != image_entrypoint:
        entrypoint_str = ' '.join(container_entrypoint) if container_entrypoint else '""'
        cmd_parts.append(f'--entrypoint {entrypoint_str}')

    # Add command if overridden or specified
    if container_cmd != image_cmd:
        cmd_str = ' '.join(container_cmd)
        cmd_parts.append(cmd_str)

    # Final command construction
    command = ' \\\n  '.join(cmd_parts)
    return command


def main():
    if len(sys.argv) != 2:
        print('Usage: python script.py <container_id_or_name>')
        sys.exit(1)

    container_id = sys.argv[1]
    reconstructed_command = reconstruct_docker_run_command(container_id)
    print(reconstructed_command)


if __name__ == '__main__':
    main()
