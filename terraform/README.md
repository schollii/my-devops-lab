The `tag_asg_managed_instances.tf` shows how one can ensure that existing AWS EC2 instances managed by an ASG will get the same tags as that ASG. This works by including a data source that finds all the EC2 instances that have the `aws:autoscaling:groupName` tag = the ASG name, then tagging each one with the tags of the ASG. The `aws:autoscaling:groupName` tag is created automatically by an ASG when it creates an EC2 instance or when an existing instance is attached to it. 

Until this module is used for a bit, use it like this: create a module on your system and copy the .tf file into it. Then, add this code (adapt to your details): 
```hcl
resource "aws_autoscaling_group" "your_asg" {
  ...
}

module "tag_managed_instances" {
  source = "./tag_managed_asg_instances"

  asg_name = aws_autoscaling_group.your_asg.name
  asg_tags = aws_autoscaling_group.your_asg.tags
}
```

The data source will be evaluated at the planning stage if the ASG name is given from a variable or local (unlike the example), in which case this only finds the EC2 instances that already exist. If those instances were created by the ASG, then the tags will already be present and the resources created will then just take ownership of those tags (except the tag "aws:autoscaling:groupName"). However if there are changes to tags on the ASG, the existing instances will automatically get the modifications. 

Note that if an instance is detached from the ASG, terraform will want to remove the tags that its former ASG has. If this is not desirable, run terraform state rm path.to.module. 
