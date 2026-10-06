"""Build a printable PDF prep report from saved comparisons, improvement plans, and interview notes.

Nothing here calls Claude: the PDF is laid out from information that has already been generated.
Three detail levels control how much of it goes in:
    sparknotes  the essentials on a page or two per posting
    simple      the main points of every section, as tables and checklists
    complete    everything, including per-requirement detail and the original posting text
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from io import BytesIO
from typing import Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from jobsearch.comparison import Action, SkillToImprove, average_match
from jobsearch.interview import SavedInterview
from jobsearch.plans import Plan
from jobsearch.reports import Report

Level = Literal["sparknotes", "simple", "complete"]
LEVEL_NAMES = {"sparknotes": "Sparknotes", "simple": "Simple", "complete": "Complete"}

# ---------- Look ----------

NAVY = colors.HexColor("#1F3A5F")
TEAL = colors.HexColor("#2A7F8F")
GREEN = colors.HexColor("#2E9E5B")
AMBER = colors.HexColor("#D98E04")
RED = colors.HexColor("#C8423B")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#5F6B7A")
LINE = colors.HexColor("#DDE3EA")
SHADE = colors.HexColor("#F3F6F9")
TRACK = colors.HexColor("#E6EAF0")

PAGE_W, PAGE_H = LETTER
MARGIN = 0.75 * inch
CONTENT_W = PAGE_W - 2 * MARGIN - 12  # the page frame pads 6pt on each side

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.5, textColor=MUTED)
BOLD = ParagraphStyle("bold", parent=BODY, fontName="Helvetica-Bold")
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=11)
CELL_RIGHT = ParagraphStyle("cellright", parent=BODY, fontSize=8.5, leading=11, alignment=TA_RIGHT)
CELL_HEAD = ParagraphStyle("cellhead", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=24, leading=28, textColor=NAVY)
SUBTITLE = ParagraphStyle("subtitle", parent=BODY, fontSize=11.5, leading=15, textColor=MUTED)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=NAVY, spaceAfter=2)
H1_SUB = ParagraphStyle("h1sub", parent=SMALL, fontSize=9, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=TEAL,
                    spaceBefore=12, spaceAfter=5)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=NAVY,
                    spaceBefore=7, spaceAfter=2)
LABEL = ParagraphStyle("label", parent=SMALL, fontName="Helvetica-Bold", fontSize=7.5, textColor=MUTED,
                       spaceBefore=4)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=1.5)
MONO = ParagraphStyle("mono", parent=BODY, fontName="Courier", fontSize=8, leading=10.5, textColor=INK)

# The PDF's built-in fonts only cover Windows-1252, so swap common symbols for plain equivalents.
REPLACEMENTS = {"→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≈": "~", "−": "-", "‑": "-",
                "✓": "", "✔": "", "★": "*", " ": " "}

GAP_LABELS = {
    "missing_skill": ("Missing skill", RED),
    "needs_more_depth": ("Needs more depth", AMBER),
    "not_shown_on_resume": ("Not shown on resume", TEAL),
}
QUESTION_TYPES = {
    "behavioral": "Behavioral", "technical": "Technical", "situational": "Situational",
    "role_specific": "Role-specific", "resume": "About your resume", "culture_fit": "Culture fit",
}
LEVEL_COLOR = {"easy": GREEN, "low": GREEN, "medium": AMBER, "hard": RED, "high": RED}
CATEGORIES = [("minimum", "Minimum"), ("preferred", "Preferred"), ("other", "Other")]


def text(s: str) -> str:
    """Escape model-written text for a Paragraph and keep it within the PDF font's characters."""
    for k, v in REPLACEMENTS.items():
        s = s.replace(k, v)
    s = s.encode("cp1252", "replace").decode("cp1252")
    return escape(s).replace("\n", "<br/>")


def color_hex(c: colors.Color) -> str:
    return "#" + c.hexval()[2:]


def tag(label: str, color: colors.Color) -> str:
    return f'<font color="{color_hex(color)}"><b>{text(label.upper())}</b></font>'


def score_color(pct: float) -> colors.Color:
    return GREEN if pct >= 75 else AMBER if pct >= 50 else RED


class Rating(Flowable):
    """A 1-5 rating drawn as five circles, filled up to the score."""

    SIZE, GAP = 6.5, 2.5

    def __init__(self, score: int):
        super().__init__()
        self.score = score
        self.width, self.height = 5 * self.SIZE + 4 * self.GAP, self.SIZE

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        c, r = self.canv, self.SIZE / 2
        c.setLineWidth(0.8)
        c.setStrokeColor(TEAL)
        for i in range(5):
            c.setFillColor(TEAL if i < self.score else colors.white)
            c.circle(r + i * (self.SIZE + self.GAP), r, r - 0.4, stroke=1, fill=1)


class CheckBox(Flowable):
    """An empty box to tick on paper, or a green checked box for items already done."""

    SIZE = 9

    def __init__(self, done: bool = False):
        super().__init__()
        self.done = done

    def wrap(self, *_):
        return self.SIZE, self.SIZE

    def draw(self):
        c, s = self.canv, self.SIZE
        c.setLineWidth(0.9)
        c.setStrokeColor(GREEN if self.done else MUTED)
        c.setFillColor(GREEN if self.done else colors.white)
        c.roundRect(0, 0, s, s, 1.5, stroke=1, fill=1)
        if self.done:
            c.setStrokeColor(colors.white)
            c.setLineWidth(1.4)
            c.lines([(s * 0.22, s * 0.52, s * 0.42, s * 0.3), (s * 0.42, s * 0.3, s * 0.8, s * 0.74)])


def checklist_item(html: str, done: bool = False) -> Table:
    """A checkbox beside a paragraph, for to-dos to work through."""
    t = Table([[CheckBox(done), Paragraph(html, BODY)]], colWidths=[16, CONTENT_W - 16])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (0, 0), 3),
                           ("TOPPADDING", (1, 0), (1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def meta_row(cells: list, widths: list[float]) -> Table:
    """A row of small labels, tags, and ratings under a heading."""
    t = Table([cells], colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def rating(label: str, score: int) -> Table:
    return meta_row([Paragraph(label, SMALL), Rating(score)], [0.42 * inch, 0.62 * inch])


def skill_meta(skill: SkillToImprove) -> Table:
    gap, color = GAP_LABELS[skill.gap_type]
    return meta_row([Paragraph(tag(gap, color), SMALL), rating("Impact", skill.impact), rating("Ease", skill.ease),
                     Paragraph(text(skill.time_estimate), SMALL)],
                    [1.45 * inch, 1.1 * inch, 1.1 * inch, CONTENT_W - 3.65 * inch])


class Bar(Flowable):
    """A horizontal score bar, filled to pct (0-100) and colored by score."""

    def __init__(self, pct: float, width: float, height: float = 7):
        super().__init__()
        self.pct, self.width, self.height = max(0.0, min(100.0, pct)), width, height

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        c, r = self.canv, self.height / 2
        c.setFillColor(TRACK)
        c.roundRect(0, 0, self.width, self.height, r, stroke=0, fill=1)
        if self.pct > 0:
            c.setFillColor(score_color(self.pct))
            c.roundRect(0, 0, max(self.height, self.width * self.pct / 100), self.height, r, stroke=0, fill=1)


def score_row(label: str, pct: float | None, label_w: float = 1.3 * inch, bar_w: float = 3.2 * inch) -> Table:
    """A label, a score bar, and the percentage on one line."""
    if pct is None:
        cells = [Paragraph(f"<b>{text(label)}</b>", CELL), Paragraph("Not in this posting", SMALL), ""]
    else:
        cells = [Paragraph(f"<b>{text(label)}</b>", CELL), Bar(pct, bar_w),
                 Paragraph(f'<font color="{color_hex(score_color(pct))}"><b>{pct:.0f}%</b></font>', CELL)]
    t = Table([cells], colWidths=[label_w, bar_w + 8, 0.6 * inch], hAlign="LEFT")
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    return t


def grid(rows: list[list], widths: list[float], header: bool = True) -> Table:
    """A table with a navy header row and light row striping."""
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY)]
    for i in range(1 if header else 0, len(rows)):
        if (i % 2 == 0) == header:
            style.append(("BACKGROUND", (0, i), (-1, i), SHADE))
    t.setStyle(TableStyle(style))
    return t


def callout(flowables: list, accent: colors.Color = TEAL) -> Table:
    """A shaded box with a colored left edge, for summaries and highlights."""
    t = Table([[flowables]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SHADE),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def bullets(items: list[str], style: ParagraphStyle = BULLET) -> list[Paragraph]:
    return [Paragraph(text(i), style, bulletText="•") for i in items]


def labeled(label: str, items: list[str]) -> list:
    """A small caps label followed by bullet points, or nothing if there are no items."""
    return [Paragraph(text(label.upper()), LABEL), *bullets(items)] if items else []


def section_heading(title: str, subtitle: str = "") -> list:
    rule = Table([[""]], colWidths=[CONTENT_W], rowHeights=[2])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 2, NAVY)]))
    return [CondPageBreak(2 * inch), Spacer(1, 10), Paragraph(text(title), H1),
            *([Paragraph(text(subtitle), H1_SUB)] if subtitle else []), rule, Spacer(1, 4)]


# ---------- Job posting match ----------

def match_section(report: Report, level: Level) -> list:
    result = report.result
    reqs = result.requirements
    out: list = [Paragraph("Resume match", H2), callout([Paragraph(text(result.overall_summary), BODY)])]

    out.append(Spacer(1, 6))
    for category, label in CATEGORIES:
        avg = average_match(result, category)
        if avg is not None or category != "other":
            out.append(score_row(f"{label} quals", avg))

    if level == "sparknotes":
        ranked = sorted(reqs, key=lambda r: -r.match_percent)
        strengths = [f"<b>{r.match_percent}%</b> · {text(r.requirement)}" for r in ranked[:3]]
        gaps = [
            f"<b>{r.match_percent}%</b> · {text(r.requirement)}"
            + (f'<br/><font color="{color_hex(MUTED)}">{text(r.weaknesses[0])}</font>' if r.weaknesses else "")
            for r in reversed(ranked[-3:])
        ]
        rows = [[Paragraph("Strongest matches", CELL_HEAD), Paragraph("Biggest gaps", CELL_HEAD)]]
        for s, g in zip(strengths + [""] * 3, gaps + [""] * 3):
            if s or g:
                rows.append([Paragraph(s, CELL), Paragraph(g, CELL)])
        out += [Spacer(1, 8), grid(rows, [CONTENT_W / 2, CONTENT_W / 2])]
        if report.work_authorization:
            out += [Spacer(1, 6), Paragraph(f"<b>Work authorization:</b> {text(report.work_authorization.summary)}", BODY)]
        return out

    if result.inferred_skills:
        out += [Paragraph("INFERRED SKILLS", LABEL), Paragraph(text(", ".join(result.inferred_skills)), BODY)]

    out.append(Paragraph("Requirements at a glance", H3))
    rows = [[Paragraph(h, CELL_HEAD) for h in ("#", "Requirement", "Type", "Match", "Talking point")]]
    for i, r in enumerate(reqs, 1):
        rows.append([
            Paragraph(str(i), CELL),
            Paragraph(text(r.requirement), CELL),
            Paragraph(text(r.category.title()), SMALL),
            [Bar(r.match_percent, 0.75 * inch, 5), Spacer(1, 2), Paragraph(f"<b>{r.match_percent}%</b>", SMALL)],
            Paragraph(text(r.talking_points[0]) if r.talking_points else "-", SMALL),
        ])
    out.append(grid(rows, [0.3 * inch, 2.45 * inch, 0.75 * inch, 0.95 * inch, CONTENT_W - 4.45 * inch]))

    if report.work_authorization:
        out += work_authorization(report, level)

    if level == "complete":
        explanations = {e.number: e for e in report.explanations}
        out.append(Paragraph("Requirement details", H2))
        for i, r in enumerate(reqs, 1):
            head = Table([[
                Paragraph(f"<b>{i}. {text(r.requirement)}</b>", BOLD),
                Paragraph(f'{tag(r.category, MUTED)}  <font color="{color_hex(score_color(r.match_percent))}">'
                          f"<b>{r.match_percent}%</b></font>", CELL_RIGHT),
            ]], colWidths=[CONTENT_W - 1.4 * inch, 1.4 * inch])
            head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                      ("LINEBELOW", (0, 0), (-1, -1), 0.6, LINE)]))
            block = [head, Spacer(1, 3), Paragraph(text(r.explanation), BODY)]
            if e := explanations.get(i):
                block += [Paragraph("IN PLAIN ENGLISH", LABEL), Paragraph(text(e.plain_english), BODY),
                          Paragraph("IN THIS ROLE", LABEL), Paragraph(text(e.in_this_role), BODY)]
                block += labeled("Key terms", [f"{t.term}: {t.definition}" for t in e.key_terms])
            block += [Paragraph("RESUME EVIDENCE", LABEL), Paragraph(text(r.context), BODY)]
            block += labeled("Talking points", r.talking_points)
            block += labeled("Strengths", r.strengths) + labeled("Weaknesses", r.weaknesses)
            out += [KeepTogether(block[:4]), *block[4:], Spacer(1, 8)]
    return out


def work_authorization(report: Report, level: Level) -> list:
    auth = report.work_authorization
    posting = auth.posting
    stance = {"accepts": ("Sponsorship accepted", GREEN), "rejects": ("No sponsorship", RED),
              "not_stated": ("Not stated", AMBER)}[posting.posting_stance]
    out = [Paragraph("Work authorization", H3),
           Paragraph(f"{tag(stance[0], stance[1])}  ·  Your status: {text(posting.status)}"
                     f"  ·  Confidence {auth.confidence}%", CELL),
           Spacer(1, 3), Paragraph(text(auth.summary), BODY)]
    out += labeled("Considerations", auth.considerations)
    if level == "complete":
        out += labeled("From the posting", posting.posting_evidence)
        if auth.h1b_matches:
            rows = [[Paragraph(h, CELL_HEAD) for h in ("Employer", "Years", "New H-1B approved / denied",
                                                       "Continuing approved / denied")]]
            for m in auth.h1b_matches:
                rows.append([Paragraph(text(m.employer), CELL), Paragraph(text(", ".join(map(str, m.years))), SMALL),
                             Paragraph(f"{m.initial_approvals} / {m.initial_denials}", CELL),
                             Paragraph(f"{m.continuing_approvals} / {m.continuing_denials}", CELL)])
            out += [Spacer(1, 4), grid(rows, [2.6 * inch, 1.3 * inch, 1.5 * inch, CONTENT_W - 5.4 * inch])]
    return out


# ---------- Interview notes ----------

def interview_id(*parts: str) -> str:
    # Same ids as pages/5_Interview_Prep.py, so checked-off items show as done.
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()[:12]


def interview_section(saved: SavedInterview, level: Level) -> list:
    plan = saved.plan
    out: list = [Paragraph("Interview notes", H2), callout([Paragraph(text(plan.summary), BODY)], NAVY)]

    prep = plan.prep[:3] if level == "sparknotes" else plan.prep
    if prep:
        out.append(Paragraph("Before the interview", H3))
        for t in prep:
            line = f"<b>{text(t.task)}</b>" + (f'  <font color="{color_hex(MUTED)}">{text(t.time_estimate)}</font>')
            if level == "complete":
                line += f"<br/>{text(t.details)}"
            out.append(checklist_item(line, interview_id("prep", t.task) in saved.done))

    recs = plan.recommendations[:3] if level == "sparknotes" else plan.recommendations
    if recs:
        out.append(Paragraph("Strategy", H3))
        if level == "sparknotes":
            out += bullets([r.title for r in recs])
        else:
            out += [Paragraph(f"<b>{text(r.title)}.</b> {text(r.details)}", BULLET, bulletText="•") for r in recs]

    if level != "sparknotes" and plan.tips:
        out.append(Paragraph("Tips for the day", H3))
        if level == "complete":
            out += [Paragraph(f"<b>{text(t.tip)}</b> — {text(t.why)}", BULLET, bulletText="•") for t in plan.tips]
        else:
            out += bullets([t.tip for t in plan.tips])

    if level == "complete" and plan.practice:
        out.append(Paragraph("Practice", H3))
        for p in plan.practice:
            out.append(checklist_item(
                f"<b>{text(p.exercise)}</b><br/>{text(p.how_to)}<br/>"
                f'<font color="{color_hex(MUTED)}" size="8">For: {text(p.related_requirement)}</font>',
                interview_id("practice", p.exercise) in saved.done))

    questions = plan.questions
    if level == "sparknotes":
        # The hardest questions are the ones most worth rehearsing.
        order = {"hard": 0, "medium": 1, "easy": 2}
        questions = sorted(questions, key=lambda q: order[q.difficulty])[:5]
    if questions:
        out.append(Paragraph("Likely questions", H3))
        for n, q in enumerate(questions, 1):
            meta = f"{tag(QUESTION_TYPES[q.type], TEAL)}  {tag(q.difficulty, LEVEL_COLOR[q.difficulty])}"
            block = [Paragraph(f"<b>Q{n}. {text(q.question)}</b>", BODY), Paragraph(meta, SMALL)]
            if level == "complete":
                block += [Paragraph("WHY THEY ASK", LABEL), Paragraph(text(q.why_they_ask), BODY)]
            if level != "sparknotes":
                block += [Paragraph("HOW TO ANSWER", LABEL), Paragraph(text(q.answer_outline), BODY)]
            if level == "complete":
                block += labeled("Draw on", q.resume_evidence)
            out += [KeepTogether(block[:4]), *block[4:], Spacer(1, 7)]
    return out


# ---------- Improvement plans ----------

def plan_action_id(skill: SkillToImprove, action: Action) -> str:
    # Same ids as pages/4_Improvement_Plan.py, so checked-off to-dos show as done.
    return hashlib.sha1(f"{skill.skill}\0{action.task}".encode()).hexdigest()[:12]


EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


def rank_skills(skills: list[SkillToImprove], easiest_first: bool) -> list[SkillToImprove]:
    if easiest_first:
        return sorted(skills, key=lambda s: (-s.ease, -s.impact))
    return sorted(skills, key=lambda s: (-s.impact, -s.ease))


def rank_actions(actions: list[Action], easiest_first: bool) -> list[Action]:
    if easiest_first:
        return sorted(actions, key=lambda a: (EFFORT_RANK[a.effort], -a.impact))
    return sorted(actions, key=lambda a: (-a.impact, EFFORT_RANK[a.effort]))


def plan_section(saved: Plan, level: Level, easiest_first: bool, report_ids: set[str]) -> list:
    plan = saved.plan
    skills = rank_skills(plan.skills_to_improve, easiest_first)
    order = "easiest to complete first" if easiest_first else "most impactful first"
    names = ", ".join(p.label for p in saved.postings)
    out: list = section_heading("Improvement plan", f"Built {saved.created_at[:10]} from: {names} · Ranked {order}")

    summary = [Paragraph(text(plan.summary), BODY)]
    if plan.strongest_areas:
        summary += [Spacer(1, 4), Paragraph(f"<b>Strongest areas:</b> {text(', '.join(plan.strongest_areas))}", BODY)]
    out.append(callout(summary, GREEN))

    all_ids = [plan_action_id(s, a) for s in skills for a in s.actions]
    if all_ids:
        finished = sum(i in saved.done for i in all_ids)
        out += [Spacer(1, 6), score_row("Plan complete", 100 * finished / len(all_ids))]

    if level == "sparknotes":
        out.append(Paragraph("Top priorities", H3))
        for n, s in enumerate(skills[:3], 1):
            top = rank_actions(s.actions, easiest_first)[:1]
            block = [Paragraph(f"<b>{n}. {text(s.skill)}</b>", BODY), Spacer(1, 2), skill_meta(s)]
            if top:
                block.append(checklist_item(f"<b>Next step:</b> {text(top[0].task)} "
                                            f'<font color="{color_hex(MUTED)}">{text(top[0].time_estimate)}</font>',
                                            plan_action_id(s, top[0]) in saved.done))
            out += [KeepTogether(block), Spacer(1, 4)]
        return out

    out.append(Paragraph("Ranking", H3))
    rows = [[Paragraph(h, CELL_HEAD) for h in ("#", "Skill", "Impact", "Ease", "Gap", "Time")]]
    for n, s in enumerate(skills, 1):
        gap, color = GAP_LABELS[s.gap_type]
        rows.append([Paragraph(str(n), CELL), Paragraph(f"<b>{text(s.skill)}</b>", CELL),
                     Rating(s.impact), Rating(s.ease),
                     Paragraph(tag(gap, color), SMALL), Paragraph(text(s.time_estimate), SMALL)])
    out.append(grid(rows, [0.3 * inch, 2.3 * inch, 0.85 * inch, 0.85 * inch, 1.3 * inch, CONTENT_W - 5.6 * inch]))

    for n, s in enumerate(skills, 1):
        block = [Paragraph(f"{n}. {text(s.skill)}", H3), skill_meta(s), Spacer(1, 3)]
        if level == "complete":
            block += [Paragraph(text(s.why_it_matters), BODY)]
        out.append(KeepTogether(block))
        if level == "complete":
            out += labeled("Related requirements", s.related_requirements)
            relevant = [(saved.posting(r.comparison), r) for r in s.relevance]
            out += labeled("Relevance to your selected postings", [
                f"{p.label}: {r.relevance}/5. {r.why}" for p, r in relevant if p and p.report_id in report_ids
            ])
            out.append(Paragraph("TO-DO", LABEL))
        for a in rank_actions(s.actions, easiest_first):
            line = (f"<b>{text(a.task)}</b>  {tag(a.effort + ' effort', LEVEL_COLOR[a.effort])}"
                    f'  <font color="{color_hex(MUTED)}">{text(a.time_estimate)}</font>')
            if level == "complete":
                line += f"<br/>{text(a.explanation)}"
            out.append(checklist_item(line, plan_action_id(s, a) in saved.done))
    return out


# ---------- Document ----------

def overview(postings: list[Report]) -> list:
    rows = [[Paragraph(h, CELL_HEAD) for h in ("Posting", "Minimum quals", "Preferred quals")]]
    for r in postings:
        cells = [Paragraph(f"<b>{text(r.job_title)}</b><br/>{text(r.company)}", CELL)]
        for category in ("minimum", "preferred"):
            avg = average_match(r.result, category)
            cells.append(Paragraph("-", SMALL) if avg is None else
                         [Bar(avg, 1.5 * inch, 6), Spacer(1, 2), Paragraph(f"<b>{avg:.0f}%</b>", SMALL)])
        rows.append(cells)
    return [Paragraph("At a glance", H2), grid(rows, [CONTENT_W - 3.6 * inch, 1.8 * inch, 1.8 * inch])]


def build_pdf(
    postings: list[Report],
    level: Level,
    include_match: bool = True,
    interviews: list[SavedInterview] = (),
    plans: list[Plan] = (),
    easiest_first: bool = False,
    posting_texts: dict[str, str] | None = None,
) -> bytes:
    """Lay out the chosen reports, interview notes, and plans as a PDF. Returns the file's bytes."""
    posting_texts = posting_texts or {}
    now = datetime.now()
    generated = f"{now:%B} {now.day}, {now.year}"
    contents = [name for name, on in [("Resume match", include_match), ("Interview notes", bool(interviews)),
                                      ("Improvement plan", bool(plans))] if on]

    story: list = [
        Paragraph("Job Prep Report", TITLE), Spacer(1, 4),
        Paragraph(text(" · ".join(f"{r.job_title} at {r.company}" for r in postings)), SUBTITLE), Spacer(1, 10),
    ]
    meta = Table([[Paragraph(f"<b>DETAIL</b><br/>{LEVEL_NAMES[level]}", SMALL),
                   Paragraph(f"<b>INCLUDES</b><br/>{text(', '.join(contents))}", SMALL),
                   Paragraph(f"<b>GENERATED</b><br/>{generated}", SMALL)]],
                 colWidths=[1.3 * inch, CONTENT_W - 2.9 * inch, 1.6 * inch])
    meta.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SHADE), ("LINEABOVE", (0, 0), (-1, 0), 2, TEAL),
                              ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                              ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story.append(meta)
    if include_match and len(postings) > 1:
        story += overview(postings)

    by_report: dict[str, list[SavedInterview]] = {}
    for s in interviews:
        by_report.setdefault(s.report_id, []).append(s)

    for i, report in enumerate(postings):
        notes = by_report.get(report.id, [])
        if not include_match and not notes:
            continue
        if i > 0 and level != "sparknotes":
            story.append(PageBreak())
        story += section_heading(f"{report.job_title}", f"{report.company} · compared {report.created_at[:10]}")
        if include_match:
            story += match_section(report, level)
        for saved in notes:
            story += interview_section(saved, level)
        if level == "complete" and (posting_text := posting_texts.get(report.id)):
            story += [CondPageBreak(2 * inch), Paragraph("Original job posting", H2), Paragraph(text(posting_text), MONO)]

    report_ids = {r.id for r in postings}
    for saved in plans:
        if level != "sparknotes":
            story.append(PageBreak())
        story += plan_section(saved, level, easiest_first, report_ids)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN, title="Job Prep Report",
                            author="Job Search", subject=", ".join(contents))
    decorate = _page_decoration(f"Job Prep Report · {LEVEL_NAMES[level]} · {generated}")
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def _page_decoration(footer: str) -> Callable:
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 6, PAGE_W, 6, stroke=0, fill=1)
        canvas.setFillColor(TEAL)
        canvas.rect(0, PAGE_H - 8, PAGE_W, 2, stroke=0, fill=1)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 0.55 * inch, PAGE_W - MARGIN, 0.55 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 0.38 * inch, footer)
        canvas.drawRightString(PAGE_W - MARGIN, 0.38 * inch, f"Page {doc.page}")
        canvas.restoreState()
    return draw
