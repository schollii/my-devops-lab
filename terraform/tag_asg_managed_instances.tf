/* 
Description: Terraform configuration file for to ensure that all existing instances 
of an ASG have the same tags as the ASG. 

Copyright (c) 2023 Oliver Schoenborn
Licensed under the MIT license (see LICENSE file)
*/
    
variable "asg_name" {
  type = string
  description = "Name of the autoscaling group (this is the name that will be in the tag automatically generated by the ASG for all instances that it manages)"
}

variable "asg_tags" {
  type = list(map(string))
  description = "The tags of the ASG"
}

locals {
  asg_tags = {for blob in var.asg_tags : blob.key => blob.value}
}

data "aws_instances" "that_managed_instances" {
  instance_tags = {
    "aws:autoscaling:groupName" = var.asg_name
  }
}

resource "aws_ec2_tag" "that_managed_instances" {
  for_each = {
    for pair in setproduct(data.aws_instances.that_managed_instances.ids, keys(local.asg_tags)) :
    "${pair[0]}.${pair[1]}" => {
      ec2_instance_id = pair[0]
      tag_key         = pair[1]
      tag_value       = local.asg_tags[pair[1]]
    }
  }

  resource_id = each.value.ec2_instance_id
  key         = each.value.tag_key
  value       = each.value.tag_value
}
