# Viva practice – VivaVerify on my own report

Generated from **Sanidhya Awasthi-23070122192-Flexi report.pdf** with the real VivaVerify planner (3 runs) and graded
by the real judge. Live vivas word questions differently every time, so learn the *reasons*, not the sentences.

## How VivaVerify questions this report
Across 3 planning runs it always covered the same four areas (different wording each time):

- **Problem and Motivation**: “Why are existing general chatbots inadequate for conducting student project vivas?” / “Why did you choose to build VivaVerify instead of using a standard chatbot for checking project reports?” / “Why do standard chatbots fail for viva examinations compared to your dedicated agent?”
- **Design Choice**: “Why did you choose BM25 instead of a vector database for evidence retrieval in VivaVerify?” / “Why did you use BM25 instead of a vector database for retrieval in VivaVerify?” / “Why did you use BM25 instead of a vector database for retrieving evidence from the uploaded report?”
- **Implementation Detail**: “How does LangGraph's interrupt mechanism facilitate the viva examination process?” / “How does the LangGraph setup handle pausing the viva workflow for a human student's answer?” / “How does the LangGraph workflow pause and wait for the student's spoken answer?”
- **Testing and Limitations**: “What issue was found during testing regarding follow-up questions, and how did you resolve it?” / “What unexpected behavior did you encounter with LLM follow-ups during testing, and how did you fix it?” / “What unexpected issue occurred during your follow-up prompt testing, and how did you fix it?”

## Model answers (each graded by the app’s judge)

### 1. Why do standard chatbots fail for viva examinations compared to your dedicated agent?
*Problem · core – asked in every run · judge score 5/5*

A general chatbot drifts away from the student's own document, so its questions aren't grounded in what I actually wrote, and it doesn't score consistently or follow up when an answer is weak. VivaVerify plans questions only from the uploaded report, grades each answer against that report, and asks one follow-up on the missing point. The AI oral-exam tools that exist are research pilots, and none support Hinglish.

### 2. Why did you use BM25 instead of a vector database for retrieval in VivaVerify?
*Design choice · core – asked in every run · judge score 5/5*

Because a student report is short, so keyword search is enough to find the right section. BM25 needs no embedding model, no extra API key and no vector database, so it is free and fast, and it is explainable because I can see which words matched. For answers that use different words, the judge also always gets the planner's evidence chunks.

### 3. How does the LangGraph workflow pause and wait for the student's spoken answer?
*Implementation · core – asked in every run · judge score 5/5*

The examiner node calls LangGraph's interrupt(). That saves the state in the checkpointer and pauses the graph, and the question goes to the browser over the WebSocket. When the answer comes back, the backend resumes the same thread with Command(resume=answer), so the graph continues from exactly that point without re-running the planner. Each viva has its own thread id.

### 4. What unexpected issue occurred during your follow-up prompt testing, and how did you fix it?
*Testing · core – asked in every run · judge score 5/5*

In the bluffer simulation one follow-up asked three questions at once and gave away the answer, the 0.21 threshold and the three seconds. A prompt is only a request, so I made the judge prompt stricter and added a code rule: a follow-up over 25 words or with more than one question mark is replaced by a single-point question. I added a unit test, and the bluffer's score fell from 20 to 0 percent because the hints were gone.

### 5. Why did you choose LangGraph instead of CrewAI or the OpenAI Agents SDK?
*Framework choice · likely variant · judge score 5/5*

A viva is a loop that has to stop and wait for a human between steps. LangGraph's interrupt and checkpointer give exactly that, and conditional edges let me keep the routing rules in my own code. CrewAI suits a team of agents finishing tasks, and the Agents SDK suits tool-calling with handoffs, not a turn-by-turn conversation.

### 6. Why is the final score calculated by code instead of by the LLM?
*Deterministic score · likely variant · judge score 5/5*

Because LLM output varies: in testing the judge gave 3 and then 2 to the same partial answer. So the LLM only judges each answer, and the reporter combines the scores in code, caps an answer that needed a follow-up at 4 out of 5, and picks the verdict from fixed bands. The mark is reproducible and cannot be hallucinated.

### 7. How do the guardrails stop a student from manipulating the examiner?
*Guardrails · likely variant · judge score 5/5*

They run in code before the LLM. One regular expression catches answers that try to instruct the examiner instead of answering, such as asking it to drop its rules or hand over top marks, and another catches empty or don't-know answers. Both score zero without any LLM call, so the student cannot talk the examiner round. In my demo run such an attempt was flagged and scored 0.

### 8. Why does VivaVerify fall back across several LLM providers?
*LLM fallback · likely variant · judge score 5/5*

Because free tiers are unreliable: Gemini's main model often returned 503 high-demand errors and OpenRouter's free tier stopped after 50 requests. So llm.py tries Gemini, then OpenRouter, then Ollama's Gemma 4, through the same OpenAI-compatible call, and a provider that fails is skipped for five minutes so it doesn't slow down every answer.

### 9. How does the voice part work, and why did you use WebSockets?
*Voice · likely variant · judge score 5/5*

The browser streams the microphone to Soniox's real-time speech-to-text over a WebSocket and shows the words as they arrive, and questions are spoken with Soniox's real-time text-to-speech the same way. WebSockets keep the delay low, so a viva feels natural. The backend only gives the browser short-lived keys, so the real API key stays on the server.

### 10. What do your evaluation results show about the judge?
*Results · likely variant · judge score 5/5*

They show it separates good and bad answers: all six fixed answers fell in the expected band, including a Hinglish one, and a prepared student scored 95 percent against 0 for a bluffer. The weakness is borderline answers: the same partial answer scored 3, 2 and 2, which matters because 3 is the pass mark. The follow-up gives those students a second chance.

## Showing a follow-up in a demo (say it out loud first)
Say: *“I’ll give a deliberately vague answer to show how the examiner follows up.”* Use **Standard** strictness.

- If asked: “Why did you use BM25 instead of a vector database for retrieval in VivaVerify?”
- Vague answer: “Because BM25 is a good algorithm and it works well for this project.” → judge gave **1/5**, below the pass mark of 3
- The app then asked: “What specific characteristics of the reports made BM25 a suitable choice?”
- Now answer properly (answer 2 above). It should score 4–5, and the question’s final score is capped at **4/5** because a follow-up was needed – point this out, it shows the deterministic reporter.
- If a different question comes first, any one-line vague answer works the same way.

## Tips
- Don’t quote attack phrases (like asking the examiner for top marks) when explaining the guardrails – describe them instead.
- Answer in 2–4 full sentences: the reason, how it works in your code, and one number or result from your report.
- Numbers worth knowing: 48 h 43 m of courses; 22/22 offline tests; 6/6 judge cases; prepared 95 % vs bluffer 0 %; judge consistency 5,5,5 and 3,2,2; voice 97.4 % word match, first audio 1.6 s; follow-up limit 1; pass marks 2/3/4; scores capped at 4/5 after a follow-up.
