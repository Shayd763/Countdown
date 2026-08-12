from cv_engine.matching import rank, score_job
from cv_engine.models import Job, Profile


def _profile():
    return Profile.from_dict(
        {
            "contact": {"name": "Test User", "email": "t@example.com"},
            "skills": ["Agile", "Scrum", "Jira", "C#", "Node.js"],
            "target_titles": ["Delivery Manager", "Scrum Master"],
        }
    )


def test_score_rewards_skill_and_title_overlap():
    profile = _profile()
    strong = Job.from_dict({
        "title": "Scrum Master",
        "description": "We need Agile, Scrum and Jira expertise.",
    })
    weak = Job.from_dict({
        "title": "Warehouse Operative",
        "description": "Lifting boxes, no software.",
    })
    score_job(strong, profile)
    score_job(weak, profile)
    assert strong.match_score > weak.match_score
    assert "Agile" in strong.matched_skills
    assert weak.match_score == 0.0


def test_special_char_skills_match_on_word_boundary():
    profile = _profile()
    job = Job.from_dict({"title": "Dev", "description": "Strong C# and Node.js."})
    score_job(job, profile)
    assert "C#" in job.matched_skills
    assert "Node.js" in job.matched_skills


def test_rank_sorts_descending():
    profile = _profile()
    jobs = [
        Job.from_dict({"title": "Nothing", "description": "unrelated"}),
        Job.from_dict({"title": "Scrum Master", "description": "Agile Scrum Jira"}),
    ]
    ranked = rank(jobs, profile)
    assert ranked[0].title == "Scrum Master"
