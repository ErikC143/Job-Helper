import hashlib

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from jobsearch import plans, reports
from jobsearch.comparison import Action, SkillToImprove, improvement_plan
from jobsearch.ui import call_claude, chat_sidebar, resume_input

load_dotenv()
st.set_page_config(page_title="Improvement Plan", page_icon="📈", layout="wide")
st.title("📈 Improvement Plan")
chat_sidebar()
st.caption("Turn your resume match results into a ranked to-do list of skills to work on.")

GAP_LABELS = {
    "missing_skill": ("Missing skill", "red"),
    "needs_more_depth": ("Needs more depth", "orange"),
    "not_shown_on_resume": ("Not shown on resume", "blue"),
}
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}
EFFORT_COLOR = {"low": "green", "medium": "orange", "high": "red"}
RANKINGS = ["Most impactful", "Easiest to complete"]


def action_id(skill: SkillToImprove, action: Action) -> str:
    return hashlib.sha1(f"{skill.skill}\0{action.task}".encode()).hexdigest()[:12]


# ---------- Ranking ----------

def rank_skills(skills: list[SkillToImprove], ranking: str) -> list[SkillToImprove]:
    if ranking == "Easiest to complete":
        return sorted(skills, key=lambda s: (-s.ease, -s.impact))
    return sorted(skills, key=lambda s: (-s.impact, -s.ease))


def rank_actions(actions: list[Action], ranking: str) -> list[Action]:
    if ranking == "Easiest to complete":
        return sorted(actions, key=lambda a: (EFFORT_RANK[a.effort], -a.impact))
    return sorted(actions, key=lambda a: (-a.impact, EFFORT_RANK[a.effort]))


def dots(score: int) -> str:
    return "●" * score + "○" * (5 - score)


# ---------- Rendering ----------

def toggle(saved: plans.Plan, aid: str) -> None:
    plans.set_done(saved, aid, st.session_state[f"todo_{saved.id}_{aid}"])


def relevance_breakdown(skill: SkillToImprove, saved: plans.Plan) -> None:
    st.markdown("##### Relevance by posting")
    if not skill.relevance:
        st.caption("Not available for this plan. Plans built before this feature don't include it.")
        return
    for entry in sorted(skill.relevance, key=lambda r: -r.relevance):
        posting = saved.posting(entry.comparison)
        with st.container(border=True):
            st.markdown(f"**{posting.label if posting else f'Posting {entry.comparison}'}**")
            st.markdown(f":violet-badge[Relevance {dots(entry.relevance)}]")
            st.caption(entry.why)


def skill_card(rank: int, skill: SkillToImprove, ranking: str, saved: plans.Plan) -> None:
    done = saved.done
    ids = [action_id(skill, a) for a in skill.actions]
    finished = sum(aid in done for aid in ids)
    check = " ✅" if ids and finished == len(ids) else ""
    label = (
        f"**#{rank} {skill.skill}**{check} · Impact {dots(skill.impact)} · "
        f"Ease {dots(skill.ease)} · {finished}/{len(ids)} done"
    )

    with st.expander(label, expanded=rank == 1):
        gap, color = GAP_LABELS[skill.gap_type]
        st.markdown(f":{color}-badge[{gap}] :gray-badge[⏱ {skill.time_estimate}]")

        why, todo = st.columns([2, 3], gap="large")
        with why:
            st.markdown("##### Why it matters")
            st.write(skill.why_it_matters)
            if skill.related_requirements:
                st.markdown("##### Related requirements")
                st.markdown("\n".join(f"- {r}" for r in skill.related_requirements))
            relevance_breakdown(skill, saved)

        with todo:
            st.markdown("##### To-do")
            if ids:
                st.progress(finished / len(ids))
            for action in rank_actions(skill.actions, ranking):
                aid = action_id(skill, action)
                with st.container(border=True):
                    # Keys include the plan id so each saved plan keeps its own checkboxes.
                    st.session_state.setdefault(f"todo_{saved.id}_{aid}", aid in done)
                    st.checkbox(
                        f"**{action.task}**",
                        key=f"todo_{saved.id}_{aid}",
                        on_change=toggle,
                        args=(saved, aid),
                    )
                    st.markdown(
                        f":{EFFORT_COLOR[action.effort]}-badge[{action.effort.title()} effort] "
                        f":violet-badge[Impact {dots(action.impact)}] "
                        f":gray-badge[⏱ {action.time_estimate}]"
                    )
                    st.caption(action.explanation)


def render_plan(saved: plans.Plan) -> None:
    plan, done = saved.plan, saved.done
    st.header(f"Plan from {saved.created_at[:16].replace('T', ' ')}")
    st.caption("Built from: " + " · ".join(p.label for p in saved.postings))
    with st.container(border=True):
        st.markdown("##### Summary")
        st.write(plan.summary)
        if plan.strongest_areas:
            st.markdown("**Strongest areas** " + " ".join(f":green-badge[{a}]" for a in plan.strongest_areas))

    all_ids = [action_id(s, a) for s in plan.skills_to_improve for a in s.actions]
    finished = sum(aid in done for aid in all_ids)
    c1, c2, c3 = st.columns(3)
    c1.metric("Skills to work on", len(plan.skills_to_improve))
    c2.metric("To-dos", len(all_ids))
    c3.metric("Completed", f"{finished}/{len(all_ids)}")
    if all_ids:
        st.progress(finished / len(all_ids), text=f"{finished / len(all_ids):.0%} of the plan complete")

    ranking = st.segmented_control("Rank by", RANKINGS, default=RANKINGS[0], required=True, key="ranking")
    skills = rank_skills(plan.skills_to_improve, ranking)

    st.markdown("##### Ranking")
    st.dataframe(
        pd.DataFrame({
            "Rank": range(1, len(skills) + 1),
            "Skill": [s.skill for s in skills],
            "Impact": [s.impact for s in skills],
            "Ease": [s.ease for s in skills],
            "Gap": [GAP_LABELS[s.gap_type][0] for s in skills],
            "Time": [s.time_estimate for s in skills],
            "Done": [
                100 * sum(action_id(s, a) in done for a in s.actions) / len(s.actions) if s.actions else 0
                for s in skills
            ],
        }),
        hide_index=True,
        column_config={
            "Skill": st.column_config.TextColumn(width="large"),
            "Impact": st.column_config.ProgressColumn(min_value=0, max_value=5, format="%d / 5"),
            "Ease": st.column_config.ProgressColumn(min_value=0, max_value=5, format="%d / 5"),
            "Done": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
        },
    )

    st.markdown("##### Skills")
    for rank, skill in enumerate(skills, 1):
        skill_card(rank, skill, ranking, saved)


# ---------- Page ----------

st.header("1. Resume")
resume_text, resume_hash = resume_input("resume_upload_plan")



def from_other_resume(report: reports.Report) -> bool:
    # Reports saved before resumes were recorded have no hash; assume they're current.
    return report.resume_hash not in ("", resume_hash)


def build_section() -> None:
    """Choose reports and build a new plan from them."""
    st.header("2. Build a plan")
    by_id = {r.id: r for r in reports.list_reports()}
    if not by_id:
        st.info("No saved reports yet. Run a comparison on the main **app** page first.")
        return

    # By default, the newest report for each job that was made from the current resume.
    default, seen = [], set()
    for report in by_id.values():  # newest first
        job = (report.job_title, report.company)
        if not from_other_resume(report) and job not in seen:
            default.append(report.id)
            seen.add(job)

    selected = st.multiselect(
        "Reports to include",
        options=list(by_id),
        format_func=lambda i: by_id[i].label + (" · older resume" if from_other_resume(by_id[i]) else ""),
        default=default,
    )
    if st.button(
        f"Build improvement plan from {len(selected)} report(s)", type="primary", disabled=not selected
    ):
        used = [by_id[i] for i in selected]
        with st.spinner("Building your improvement plan... this can take a minute or two."):
            plan = call_claude(improvement_plan, resume_text, [r.result for r in used])
        st.session_state["plan_pick"] = plans.save(plan, resume_hash, used).id
        st.rerun()
    st.caption("Each plan is saved permanently as JSON. Open earlier plans from the sidebar.")


build_section()

# Sidebar: every saved plan, newest first.
with st.sidebar:
    st.header("Saved plans")
    all_plans = plans.list_plans()
    if not all_plans:
        st.caption("No plans yet. Build one to see it here.")
        st.stop()
    plans_by_id = {p.id: p for p in all_plans}
    if st.session_state.get("plan_pick") not in plans_by_id:
        st.session_state["plan_pick"] = all_plans[0].id
    picked = plans_by_id[st.radio(
        "Open a plan", list(plans_by_id), key="plan_pick", format_func=lambda i: plans_by_id[i].label,
    )]
    st.caption(f"Stored as inputs/plans/{picked.id}.json")
    st.download_button(
        "Download JSON", plans.to_json(picked), file_name=f"{picked.id}.json", mime="application/json"
    )
    if st.button("Delete this plan"):
        plans.delete(picked.id)
        del st.session_state["plan_pick"]
        st.rerun()

st.divider()
if picked.resume_hash != resume_hash:
    st.caption("This plan was built from a different version of your resume.")
render_plan(picked)
