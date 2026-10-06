terraform {
  required_version = ">= 1.5.0" # `import` blocks land in 1.5

  required_providers {
    axiom = {
      source  = "axiomhq/axiom"
      version = "~> 1.6"
    }
  }
}
