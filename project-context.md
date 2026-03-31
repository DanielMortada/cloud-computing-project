# INFO-H505 Project: SmartStudy Cloud Agent

## 1. Project Overview & Preparation

This project involves building a production-grade Al application on Google Cloud Platform (GCP). The process is split into two distinct phases:

1. Preparation (The Sandbox): Self-study using the Google Skills environment. This uses Google Skills Credits (temporary, virtual).
2. Implementation (The Real Cloud): Building your final project in the live Google Cloud Console. This uses GCP Credits (real monetary value).

**Action 1: Register for Google Skills (Sandbox)**

*Step 1: Create your Account*
Go to the Google Skills Sign-up page. Important: You must select "Continue with email and password" and use your ULB/VUB email address. Using a personal Google account will prevent you from accessing the course cohort and the shared credit pool.

*Step 2: Join the Student Cohort*
Once you have created your account, I will send a formal invitation to your university email from <noreply@skills.google>. Click the link in that email to join our specific cohort. This unlocks the curriculum and the shared lab credits.

**Action 2: Retrieve Google Cloud Credits (Real Cloud)**

To build your final project, you need a live GCP environment. Redeem a USD 50 coupon by following the instructions at the Student Coupon Retrieval Link.

* Requirements: Use your school domain (@ulb.be or @vub.be).
* Confirmation: You will receive a coupon code via email. This code adds a USD 50 balance to your personal Google Cloud Billing account for this course.

---

## 2. Self-Study Curriculum

Before starting your implementation, you are expected to complete the following modules. These provide the architectural "blueprints" for your project.

1. Foundations of Generative Al: Complete the Introduction to Generative Al course. Focus on Large Language Models (LLMs) and the concept of "grounding" to prevent model hallucinations.
2. Event-Driven Design: Review the documentation for Google Cloud Storage (GCS) and Cloud Functions. Understand how a "Finalize" event in a bucket can automatically trigger code.
3. Vector Search & Embeddings: Complete the Vector Search and Embeddings course. Learn how RAG uses mathematical vectors to perform semantic searches rather than simple keyword matching.
4. Hands-on Lab (Baseline): Complete the Creating a RAG Chat Assistant with MongoDB Atlas Vector Search, Google Cloud and Langchain lab. Do not pay for this lab: the "Start Lab" button will automatically draw from our class "Share Group" credits. While completing the lab, copy and save your Python code. You will need the logic from this lab to build your final project in the real cloud.

Note: If you cannot access the courses or labs, visit the Catalog and search for them by name:

* Introduction to Generative AI
* Vector Search and Embeddings
* Creating a RAG Chat Assistant with MongoDB Atlas Vector Search, Google Cloud and Langchain

---

## 3. Project Description

**The Mission**
Build SmartStudy, an automated, cloud - native "Tutor" that allows a user to upload lecture PDFs to a cloud folder and immediately engage in a context-aware chat session to prepare for exams.

**Main Features (Required)**

* Ingestion: Create a Google Cloud Storage (GCS) bucket for PDF uploads.
* Automated Pipeline: Deploy a Cloud Function (Python) triggered by GCS uploads that:
  * Extracts text from the PDF.
  * Chunks the text using LangChain.
  * Generates embeddings via Vertex Al Text Embeddings API.
  * Upserts the vectors into a MongoDB Atlas Vector Search index.
* Orchestration: Use LangChain to manage the Retrieval-Augmented Generation (RAG) loop.
* Generative Model: Use Gemini 2.5 Flash for generating responses.
* Tutor Persona: Implement a "System Instruction" that requires the Al to act as a Formal Academic Tutor (citing sources, summarizing, and asking pedagogical follow-up questions).

**Advanced Features (Choose One)**

To reach the maximum grade, implement at least one of the following:

* Multi-Modal Intelligence: Enable Gemini to analyze images, charts, and diagrams found within the PDFs.
* Interactive Quiz Mode: Create a /quiz command that generates a 5-question quiz based on the uploaded material.
* Conversation Memory: Store chat history in MongoDB so the tutor "remembers" previous student interactions.
* Web Interface: Deploy a Ul using Streamlit or Cloud Run for a professional user experience.

---

## 4. Evaluation Rubric (Total: 6 Points)

* Main Features: 4/6 Points
* Advanced Feature: 2/6 Points

| Category | Excellent (A) | Good (B) | Satisfactory (C) |
| :--- | :--- | :--- | :--- |
| **Cloud Automation (1 pt)** | Full Pipeline: Uploading to GCS triggers the entire ingestion process automatically. | Partial: Cloud Functions are used, but some data syncing requires manual script triggers. | No Automation: Processes are run via local scripts; Cloud is used only for storage. |
| **Retrieval Accuracy (1 pt)** | Grounded & Cited: Al provides accurate answers and always cites the specific source file/page. | Accurate but Vague: Answers are correct but lack specific citations or references. | Hallucinations: Al ignores the notes and answers using general internet knowledge. |
| **System Architecture (1 pt)** | Modular & Robust: Uses LangChain LCEL. Includes error handling for failed PDF parses or API timeouts. | Functional: The code works but is monolithic. Limited error handling for edge cases. | Fragile: Hardcoded paths/names; system crashes if input format varies. |
| **Tutor Persona (1 pt)** | Pedagogical: Acts as a mentor - offers study tips and checks for student understanding. | Standard Chat: Clear and helpful, but lacks a specific teaching persona. | Passive: Acts as a basic search engine; dry and non-interactive responses. |
| **Advanced Feature (2 pts)** | Exceptional: Feature is fully functional, seamlessly integrated, and handles errors gracefully. | Functional: Feature works well but has minor bugs or lacks deep integration. | Basic: Feature is present but experimental, or only partially implemented. |

---

## 5. Submission Requirements

Submit your project via UV by May 17 as a single PDF report containing:

1. GitHub Repository Link: Must contain your Cloud Function and orchestration code.
2. Architectural Diagram: A clear PDF visualization of your system's data flow.
3. Implementation Summary: A concise description of your work, specifically highlighting the technical changes you made when moving from the Lab to the live Cloud environment.

Presentation: During the final week, you will present your work via slides and a live demonstration.
