#!/usr/bin/env python3
"""
Converts the OLD saved_questions/{username}/... tree into the NEW
three-node structure used by test__2_.html:

    userSaved/{username}/{subject}/{topic}/{quizId}/{questionId} = true
    userSavedByQuiz/{username}/{quizId}/{questionId} = {s, t, at}
    allQuestions/{quizId}/{questionId} = <full question json, written once>

Old shapes handled (from saved.js parseRawTree):
  1) saved_questions/{user}/{subjectKey}/{qKey} -> {quiz_id, question_data, saved_at}
  2) saved_questions/{user}/{qKey} -> {quiz_id, question_data, saved_at}   (legacy, no subject)

Quiz-ID cleaning mirrors saved.js getDynamicUrl(): strips known subject/section
suffixes and a trailing -part-N, because the new html sends quiz IDs without them.
"""
import json
import re
import sys
import os

INPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "old_saved_questions.json"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "converted"

os.makedirs(OUT_DIR, exist_ok=True)

TOPIC_DEFAULT = "All Topic"

SUFFIXES = [
    "-mathandreasoning", "-englishandgk", "-generalawareness",
    "-maths", "-reasoning", "-english", "-gk", "-math",
    "-section1", "-section2", "-section3",
]

SUBJECT_ALIASES = {
    "MATH": "MATHS", "MATHS": "MATHS", "MATHEMATICS": "MATHS",
    "REASONING": "REASONING",
    "GK": "GK", "GS": "GK", "GENERALSTUDIES": "GK", "GENERALAWARENESS": "GK",
    "ENGLISH": "ENGLISH",
    "HINDI": "HINDI",
    "COMPUTER": "COMPUTER",
}


def clean_quiz_id(quiz_id: str) -> str:
    if not quiz_id:
        return quiz_id
    lower = quiz_id.lower()
    clean = quiz_id
    for suf in SUFFIXES:
        if lower.endswith(suf):
            clean = quiz_id[: len(quiz_id) - len(suf)]
            break
    clean = re.sub(r"-part-\d+$", "", clean, flags=re.IGNORECASE)
    return clean


def normalize_subject(raw_key: str) -> str:
    if not raw_key:
        return "Miscellaneous"
    u = re.sub(r"[.#$\[\]/]", "", raw_key.strip().upper())
    return SUBJECT_ALIASES.get(u, raw_key.strip().upper())


def fb_key(raw: str) -> str:
    if raw is None:
        return ""
    s = re.sub(r"\s+", " ", str(raw).strip())
    for ch in ".#$[]/":
        s = s.replace(ch, "-")
    return s[:60]


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    user_saved = {}
    user_saved_by_quiz = {}
    all_questions = {}

    total_entries = 0
    per_user_counts = {}

    def add_entry(username, subject_raw, quiz_id_raw, q_key, question_data, saved_at):
        nonlocal total_entries
        if not question_data:
            return
        qid = fb_key(str(question_data.get("id", q_key)))
        quiz_id = fb_key(clean_quiz_id(quiz_id_raw or ""))
        uname = fb_key(username)
        subject = fb_key(subject_raw)
        topic = fb_key(TOPIC_DEFAULT)
        if not (uname and quiz_id and subject and qid):
            return

        user_saved.setdefault(uname, {}).setdefault(subject, {}).setdefault(
            topic, {}
        ).setdefault(quiz_id, {})[qid] = True

        user_saved_by_quiz.setdefault(uname, {}).setdefault(quiz_id, {})[qid] = {
            "s": subject_raw,
            "t": TOPIC_DEFAULT,
            "at": saved_at or 0,
        }

        bank_quiz = all_questions.setdefault(quiz_id, {})
        if qid not in bank_quiz:
            payload = dict(question_data)
            payload["quiz_id"] = quiz_id_raw and clean_quiz_id(quiz_id_raw) or quiz_id
            payload["subject"] = subject_raw
            payload["topic"] = TOPIC_DEFAULT
            bank_quiz[qid] = payload

        total_entries += 1
        per_user_counts[username] = per_user_counts.get(username, 0) + 1

    for username, user_tree in raw.items():
        if not isinstance(user_tree, dict):
            continue
        for top_key, top_val in user_tree.items():
            if not isinstance(top_val, dict):
                continue
            if "question_data" in top_val:
                # Legacy leaf — no subject bucket present at all
                add_entry(
                    username,
                    "Miscellaneous",
                    top_val.get("quiz_id"),
                    top_key,
                    top_val.get("question_data"),
                    top_val.get("saved_at"),
                )
            else:
                # Subject bucket
                subject_raw = normalize_subject(top_key)
                for q_key, q_val in top_val.items():
                    if not isinstance(q_val, dict) or "question_data" not in q_val:
                        continue
                    add_entry(
                        username,
                        subject_raw,
                        q_val.get("quiz_id"),
                        q_key,
                        q_val.get("question_data"),
                        q_val.get("saved_at"),
                    )

    # ---- write output files ----
    out_user_saved = os.path.join(OUT_DIR, "converted-userSaved.json")
    out_by_quiz = os.path.join(OUT_DIR, "converted-userSavedByQuiz.json")
    out_all_q = os.path.join(OUT_DIR, "converted-allQuestions.json")

    with open(out_user_saved, "w", encoding="utf-8") as f:
        json.dump(user_saved, f, ensure_ascii=False)
    with open(out_by_quiz, "w", encoding="utf-8") as f:
        json.dump(user_saved_by_quiz, f, ensure_ascii=False)
    with open(out_all_q, "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False)

    # ---- report ----
    unique_questions = sum(len(v) for v in all_questions.values())
    top_users = sorted(per_user_counts.items(), key=lambda x: x[1], reverse=True)[:50]
    quiz_question_counts = sorted(
        ((qid, len(qs)) for qid, qs in all_questions.items()),
        key=lambda x: x[1], reverse=True
    )[:300]

    old_size = os.path.getsize(INPUT_PATH)
    new_sizes = {
        "userSaved": os.path.getsize(out_user_saved),
        "userSavedByQuiz": os.path.getsize(out_by_quiz),
        "allQuestions": os.path.getsize(out_all_q),
    }
    new_total = sum(new_sizes.values())

    def human(n):
        for unit in ["B", "KB", "MB", "GB"]:
            if n < 1024:
                return f"{n:.2f} {unit}"
            n /= 1024
        return f"{n:.2f} TB"

    report_lines = []
    report_lines.append("# Saved Questions Conversion Report\n")
    report_lines.append(f"- Total saved-question entries processed: **{total_entries}**")
    report_lines.append(f"- Unique questions in new question bank: **{unique_questions}**")
    report_lines.append(f"- Total users: **{len(per_user_counts)}**\n")

    report_lines.append("## File size change\n")
    report_lines.append(f"- Old raw export: **{human(old_size)}**")
    report_lines.append(f"- New userSaved.json: **{human(new_sizes['userSaved'])}**")
    report_lines.append(f"- New userSavedByQuiz.json: **{human(new_sizes['userSavedByQuiz'])}**")
    report_lines.append(f"- New allQuestions.json: **{human(new_sizes['allQuestions'])}**")
    report_lines.append(f"- New total (all 3 files): **{human(new_total)}**")
    if old_size > 0:
        pct = (1 - (new_total / old_size)) * 100
        report_lines.append(f"- Size reduction: **{pct:.1f}%**\n")

    report_lines.append("## Top 50 users by saved-question count\n")
    report_lines.append("| Rank | Username | Saved Questions |")
    report_lines.append("|---|---|---|")
    for i, (uname, count) in enumerate(top_users, 1):
        report_lines.append(f"| {i} | {uname} | {count} |")

    report_lines.append("\n## Top 300 quiz IDs by unique question count\n")
    report_lines.append("| Rank | Quiz ID | Questions in bank |")
    report_lines.append("|---|---|---|")
    for i, (qid, count) in enumerate(quiz_question_counts, 1):
        report_lines.append(f"| {i} | {qid} | {count} |")

    report_text = "\n".join(report_lines) + "\n"
    report_path = os.path.join(OUT_DIR, "conversion-report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Also echo to GitHub Actions step summary if available
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report_text)

    print(report_text)


if __name__ == "__main__":
    main()
