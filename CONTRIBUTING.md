# Contributing

1. Fork and clone the repo, then follow the Quick start in the README.
2. Create a branch for your change.
3. Run the tests with `pytest` (install it with `pip install pytest`). The tests don't call the
   Claude API, so they're free to run.
4. Open a pull request describing what you changed and why.

Never commit your `.env` file or anything in `inputs/` or `data/`: they hold your API key and
personal job search data, and are git-ignored for that reason.
