# VivaVerify live evaluation

## A. Judge accuracy (one question, answers of known quality)

| Case | Expected score | Scores (repeats) | Pass | Provider |
|---|---|---|---|---|
| correct | 4-5 | [5, 5, 5] | yes | openrouter |
| partial | 1-3 | [3, 2, 2] | yes | openrouter |
| wrong | 0-2 | [0] | yes | openrouter |
| hinglish | 4-5 | [5] | yes | openrouter |
| no answer | 0-0 | [0] | yes | guardrail |
| manipulation | 0-0 | [0] | yes | guardrail |

## B. Simulated students (full viva on the sample report)

| Student | Overall | Verdict | Follow-ups asked | Time (s) | Providers |
|---|---|---|---|---|---|
| prepared | 95% | Strong understanding | 0 | 128 | openrouter |
| bluffer | 0% | Weak understanding - key parts of the report could not be defended | 4 | 115 | openrouter |

### Trace: prepared

1. Planner: Read the report and planned 4 questions (Problem Motivation, Design Choice, Liveness Check, Testing Results) via openrouter.
2. Examiner: Answer received.
3. Judge: Scored 5/5 via openrouter.
4. Router: Moving to question 2.
5. Examiner: Answer received.
6. Judge: Scored 4/5 via openrouter.
7. Router: Moving to question 3.
8. Examiner: Answer received.
9. Judge: Scored 5/5 via openrouter.
10. Router: Moving to question 4.
11. Examiner: Answer received.
12. Judge: Scored 5/5 via openrouter.

Questions asked:
- What problem does the system aim to solve and why is it important for lectures?
- Why did you choose the HOG face detector over the CNN detector for this project?
- How does the liveness check prevent proxy attendance using a printed photo or phone screen?
- What were the recognition accuracy results across the three lighting conditions tested?

### Trace: bluffer

1. Planner: Read the report and planned 4 questions (Problem motivation, Design choice, Liveness implementation, Testing results) via openrouter.
2. Examiner: Answer received.
3. Judge: Scored 0/5 via openrouter.
4. Router: Below pass mark (3/5), so asking one follow-up on the missing point.
5. Examiner: Answer received.
6. Judge: Scored 0/5 via openrouter.
7. Router: Moving to question 2.
8. Examiner: Answer received.
9. Judge: Scored 0/5 via openrouter.
10. Router: Below pass mark (3/5), so asking one follow-up on the missing point.
11. Examiner: Answer received.
12. Judge: Scored 0/5 via openrouter.
13. Router: Moving to question 3.
14. Examiner: Answer received.
15. Judge: Scored 0/5 via openrouter.
16. Router: Below pass mark (3/5), so asking one follow-up on the missing point.
17. Examiner: Answer received.
18. Judge: Scored 0/5 via openrouter.
19. Router: Moving to question 4.
20. Examiner: Answer received.
21. Judge: Scored 0/5 via openrouter.
22. Router: Below pass mark (3/5), so asking one follow-up on the missing point.
23. Examiner: Answer received.
24. Judge: Scored 0/5 via openrouter.

Questions asked:
- What specific problems with manual roll call motivated the development of your automatic attendance system?
- What time‑saving issue with manual roll call did the project aim to solve?
- Why did you choose the HOG face detector over the CNN detector for your Raspberry Pi implementation?
- What advantage does the HOG detector provide on the Raspberry Pi 4 CPU compared to the CNN detector?
- How does the blink‑based liveness check work to prevent proxy attendance?
- Can you explain how the eye aspect ratio is used to detect a blink for the liveness check?
- What were the recognition accuracy results under different lighting conditions during your tests?
- What recognition accuracy did you achieve in normal classroom light?
