"""Builds the end-to-end tests' vault from data/library, with no real model."""

from data.build import seed
from testing.e2e_worker import use_stand_ins

if __name__ == "__main__":
    use_stand_ins()
    seed.main()
