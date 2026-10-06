# The Axiom provider reads its credential from AXIOM_API_TOKEN (CI: the
# AXIOM_TF_API_TOKEN repo secret). That token is the bootstrap credential:
# created by hand in the Axiom UI and deliberately NOT managed here, because a
# config that manages its own credential can lock itself out.
provider "axiom" {}
