# Job Search

A local-first job search assistant built with [Streamlit](https://streamlit.io) and the
[Claude API](https://platform.claude.com). Upload your resume once, compare it against job
postings, and get a plan for closing your gaps and preparing for interviews.

Everything you upload or generate stays on your own machine. The only thing that leaves it is
the text sent to the Claude API when you run an analysis.

## Features

- **Resume Match** (`app.py`): scores your resume against each requirement in a job posting
  (minimum, preferred, and other), with talking points, strengths, and weaknesses for each one.
  It can also explain what each requirement means in plain English, and check whether your
  work authorization fits the posting, using public USCIS H-1B data on the employer.
- **Improvement Plan**: turns one or more comparisons into a ranked to-do list of skills to
  work on, showing how relevant each skill is to each job posting.
- **Interview Prep**: builds an interview plan for a posting, with prep tasks,
  recommendations, tips, practice exercises, and likely questions with answer outlines drawn
  from your resume.
- **Generate Report**: bundles saved results into a printable PDF at three levels of detail.
  This doesn't call the API, so it's free.

Your resume and job postings are saved once and shared by every page. Comparisons and interview
plans run in the background, so refreshing the page doesn't stop them.

## Requirements

- Python 3.10 or newer
- A Claude API key from [platform.claude.com](https://platform.claude.com) with credits on the
  account. A Claude.ai chat subscription doesn't include API access.

## Quick start

```bash
git clone <repo-url> job-search
cd job-search
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

Open `.env` and paste your API key after `ANTHROPIC_API_KEY=`. Then start the app:

```bash
streamlit run app.py
```

It opens at http://localhost:8501. Upload your resume as a PDF or TXT file, add a job posting,
and click **Compare**. The other features are in the sidebar.

## Cost

Each analysis is a call to Claude Sonnet 5.5, billed to your API account. A comparison typically
costs a few cents. Interview plans and improvement plans cost a bit more because their output is
longer. Saved results are reused rather than regenerated, and you can set a monthly spend limit
in the Claude Console.

### Using your Claude subscription instead (personal use)

If you have a Claude Pro or Max plan and [Claude Code](https://claude.com/claude-code) installed
and signed in, you can send every request through it instead of the API. Requests then count
against your plan's usage limits rather than being billed to an API key. Add this line to `.env`:

```
CLAUDE_BACKEND=cli
```

The app runs `claude -p` with no tools, so Claude can only answer, not touch your files, and
leaves `ANTHROPIC_API_KEY` out of its environment so the key isn't billed. This is meant for
your own copy of the app. Running several comparisons at once uses your plan's limits quickly.

## Your data

Everything is stored in the project folder, and these folders are git-ignored so they're never
committed:

| Location | Contents |
|---|---|
| `inputs/resume.txt` | Your resume as text, plus any candidate notes |
| `inputs/reports/` | Comparison reports (JSON) |
| `inputs/plans/` | Improvement plans and your checked-off to-dos (JSON) |
| `inputs/interviews/` | Interview plans and your progress (JSON) |
| `data/jobsearch.db` | Saved job postings (SQLite) |
| `data/*.csv` | USCIS H-1B data, downloaded the first time the work authorization check runs |
| `.env` | Your API key |

To move your data to another machine, copy the `inputs/` and `data/` folders.

## Command line

You can compare a resume and a posting without the web app:

```bash
python -m jobsearch.comparison resume.txt posting.txt          # Markdown report
python -m jobsearch.comparison resume.txt posting.txt --json   # raw JSON
```

## Project layout

```
app.py                     Resume Match page (the home page)
pages/
  4_Improvement_Plan.py    Ranked skills to work on
  5_Interview_Prep.py      Interview plans
  6_Generate_Report.py     Printable PDF reports
jobsearch/
  comparison.py            Claude prompts and output schemas; command-line entry point
  llm.py                   Sends requests through the Claude API or the Claude Code CLI
  interview.py             Interview plan prompt, schema, and storage
  reports.py, plans.py     Saving and loading reports and improvement plans
  resume.py                The saved resume and candidate notes
  jobs.py                  Background jobs that survive page refreshes
  h1b.py                   USCIS H-1B Employer Data Hub lookup
  pdf_report.py            PDF layout
  ui.py                    Streamlit components shared by the pages
  db.py                    SQLite storage for saved postings
  sources/                 Job source plugin interface (not yet used by the app)
tests/                     pytest tests
```

## Limitations

- Background jobs run inside the Streamlit server. Stopping `streamlit run`, or saving a code
  change while it's running, cancels any analysis in progress.
- Scanned PDFs without a text layer can't be read. Convert them to text first.
- The work authorization check is guidance based on the posting's wording and public H-1B
  data, not legal advice.

## Development

```bash
pip install pytest
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests are welcome.

## License

MIT, see [LICENSE](LICENSE).
