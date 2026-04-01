# SmartStudy Architecture - High-Level Overview

Last updated: 2026-04-01

This is the quick, non-technical view of what the system does today.

## Big Picture

SmartStudy is an AI tutor that lets a student upload lecture PDFs, then ask questions grounded in those documents.

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryTextColor":"#202124","lineColor":"#5F6368","fontFamily":"Arial"}}}%%
flowchart LR
    A[Student] -->|0. Open app| B[SmartStudy UI]

    B -->|1. Upload one or more PDFs| C[(GCS Bucket)]
    C -->|2. Finalize event| G[Ingestion Function]
    G -->|3. Upsert vectors| D[(MongoDB Vector Knowledge Base)]

    B -->|4. Poll readiness| S[Status API]
    S -->|5. Check chunk presence| D
    D -->|6. Return document status| S
    S -->|7. Show ready or processing| B

    B -->|8. Ask question| X[Chat API]
    X -->|9. Run vector search| D
    D -->|10. Return relevant chunks| X
    X -->|11. Generate grounded answer| E[Gemini Model]
    E -->|12. Return model output| X
    X -->|13. Answer + citations| B

    C -->|A1. Delete event| H[Cleanup Function]
    H -->|A2. Delete vectors| D

    classDef user fill:#E8F0FE,stroke:#4285F4,color:#1A73E8,stroke-width:1px;
    classDef service fill:#E6F4EA,stroke:#34A853,color:#188038,stroke-width:1px;
    classDef compute fill:#FEF7E0,stroke:#FBBC05,color:#EA8600,stroke-width:1px;
    classDef data fill:#FCE8E6,stroke:#EA4335,color:#C5221F,stroke-width:1px;

    class A user;
    class B,X,S service;
    class G,H,E compute;
    class C,D data;
```

## Main User Journey

1. Student selects one or more PDFs and uploads them in one batch from the UI.
2. Each file is stored in the cloud bucket with a unique object name.
3. An ingestion function automatically processes each PDF:
   - reads text
   - chunks text
   - creates embeddings
   - stores vectors in MongoDB
4. UI polls document status and shows per-file readiness in the interface.
5. Student asks a question in chat.
6. Chat API retrieves relevant chunks from MongoDB.
7. Gemini generates an answer with citations.
8. UI displays answer + sources.

## Main Features Already Working

- Cloud-native upload pipeline from UI to GCS.
- Batch multi-PDF upload from one UI action.
- Automatic ingestion from GCS events.
- Live per-document readiness notifications in UI via status polling.
- Vector search on MongoDB Atlas.
- Grounded Q&A with source citations.
- Quiz command support in tutor prompt (`/quiz`).
- Automatic cleanup of vectors when PDFs are deleted from GCS.

## Why This Architecture Is Good for the Project

- It is event-driven and automated (no manual ingestion step for normal use).
- It follows RAG design principles (retrieve first, then generate).
- It is modular:
  - UI (Streamlit)
  - API/orchestration (Flask + LangChain)
  - ingestion/cleanup (Cloud Functions)
  - storage/search (MongoDB Atlas)
- It matches the project requirements for cloud automation, retrieval, and tutor persona.

## Current Deployed Endpoints

- UI: `https://smartstudy-ui-omcgx7zncq-ew.a.run.app`
- Chat API: `https://smartstudy-chat-api-omcgx7zncq-ew.a.run.app`

## Current Limitations (Known)

- Refreshing the page resets the local UI chat transcript (backend history exists but is not fully rehydrated into UI yet).
- If multiple PDFs are active, citation lists may show multiple files by design.

## Next Evolution (When Needed)

- Persistent frontend session identity across refresh.
- Better document management UI (list/delete/select active docs).
- Per-user document isolation and filtering.
