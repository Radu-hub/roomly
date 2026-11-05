resource "aws_ecr_repository" "roomly" {
  name                 = "roomly"
  image_tag_mutability = "IMMUTABLE" # a commit's tag can never point at different code
  force_delete         = true        # lets `terraform destroy` finish cleanly

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "roomly" {
  repository = aws_ecr_repository.roomly.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
