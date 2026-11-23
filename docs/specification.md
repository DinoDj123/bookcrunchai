# bookcrunch.ai — Software Specification v1.5 (Markdown copy)

Team Bookcrunch.ai · Daris Velović, Dino Đerekerac · Mentors: Ana Pješivac, Tamara Pavlović · 23.11.2026

> This is a text copy of `bookcrunch_software_specification_v1.5.docx` so that Claude Code can read it.
> The Word document is the official version sent to the mentors; diagrams and wireframes are only there.
> When the Word document changes, regenerate this file.

# 1. Introduction

## 1.1 Project Overview

bookcrunch.ai is a web application that turns a long book into something a reader can check and use quickly. A user uploads a book in EPUB or text-based PDF format. The system extracts its structure, detects chapters, and uses a large language model to produce summaries at several levels: a one-paragraph overview, chapter-by-chapter summaries and a list of key ideas, at a reading length the user selects (a 5-minute or a 15-minute read). Summaries can be produced in English or in Montenegrin, regardless of the language the book is written in.

Users can also ask questions about the book. Answers are generated only from passages retrieved from the book, and every answer shows the cited passages with a link to the chapter they come from, so each claim can be checked against the source.

Existing summary services such as Blinkist and Shortform offer a fixed catalogue of human-written summaries, mostly of English-language business books. They do not summarise a user’s own books, do not support local languages and do not let the user verify claims. bookcrunch.ai is aimed at readers who want the key ideas of a book before reading it or when coming back to it, and at students who study a required text. The project puts particular emphasis on faithfulness: measuring and reducing statements that the book does not actually support.

## 1.2 Purpose of This Document

This document defines the software requirements, architecture, data model, interfaces and constraints for bookcrunch.ai. It is the reference for the two-member development team during the four-week practical phase of AI-DEV 2026, the basis for the mentor’s approval before major implementation starts, and the place where significant design decisions and rejected alternatives are recorded. It is kept up to date as the project evolves (see Section 12).

## 1.3 Scope

This document covers the following aspects of the system:

- Functional and non-functional requirements

- System architecture and component design

- Data model and database design

- API specifications

- User interface design

- Testing strategy

- Deployment plan

**In scope (MVP):** upload and parsing of EPUB and text-based PDF; chapter detection and table of contents; hierarchical summarisation with selectable length and output language; key ideas linked to chapters; question answering with cited passages; processing status; token and cost tracking per book; caching so that a book is processed only once; a small faithfulness evaluation on 2–3 public-domain books.

**Out of scope (stretch goals, only after the MVP works):** audio version of the summary, flashcards generated from key ideas, comparison of two books, and scanned-PDF support via OCR. Also out of scope: payments, social features, sharing of uploaded books between users, mobile apps and any public launch (which would need a legal review).

## 1.4 Definitions and Abbreviations

| **Term**                 | **Definition**                                                                                                         |
|--------------------------|------------------------------------------------------------------------------------------------------------------------|
| API                      | Application Programming Interface                                                                                      |
| UI                       | User Interface                                                                                                         |
| DB                       | Database                                                                                                               |
| MVP                      | Minimum Viable Product: the Must Have scope defined in the project brief                                               |
| LLM                      | Large Language Model (here: Claude models accessed through the Claude API)                                             |
| RAG                      | Retrieval-Augmented Generation: the model answers from passages retrieved from the book instead of from its own memory |
| EPUB                     | Electronic publication format; a ZIP archive of XHTML documents with a table of contents                               |
| TOC                      | Table of Contents                                                                                                      |
| Chunk                    | A passage of the book made of whole paragraphs from one chapter; the unit that is embedded, retrieved and cited        |
| Embedding                | A numeric vector that represents the meaning of a text, used for similarity search                                     |
| pgvector                 | PostgreSQL extension that stores embeddings and performs vector similarity search                                      |
| Map-reduce summarisation | Summarising each chapter separately (map) and then combining the chapter results into a book summary (reduce)          |
| Chapter notes            | The output of the map step: a detailed, length-independent summary of one chapter, generated once per book             |
| Faithfulness             | The share of summary claims that are supported by the source book                                                      |
| Citation                 | A reference from an answer to the chunk (chapter and passage) that supports it                                         |
| JWT                      | JSON Web Token, used to authenticate API requests                                                                      |
| cnr                      | ISO 639 language code for Montenegrin; en is used for English                                                          |

# 2. Stakeholders and Users

## 2.1 Stakeholder Identification

| **Stakeholder**                                | **Interest in the project**                                                                                                                   |
|------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| Readers                                        | Want the key ideas of their own books quickly, in English or Montenegrin, and want to be able to trust the summary.                           |
| Students                                       | Use chapter summaries and cited answers to study a required text and need to find the exact passage behind an answer.                         |
| Development team (2 full-stack developers)     | Design, build, test, deploy and present the system; both members must be able to explain every part of it.                                    |
| Mentor                                         | Approves the specification and any change of stack, follows weekly progress, evaluates the final result.                                      |
| DevelopersLab, Coinis (AI-DEV 2026 organisers) | Run the programme and define the evaluation criteria and required artefacts.                                                                  |
| Authors and publishers (indirect)              | Their copyright must be respected: uploaded books stay private to the uploader and the shared demo library contains only public-domain books. |

## 2.2 User Roles and Personas

Reader and Student are two personas of the same technical role (an authenticated user). There is no administrator interface in the MVP; demo books are loaded with a seed script.

| **Role** | **Description**                                                                                                   | **Key Needs**                                                                                                               |
|----------|-------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| Reader   | Authenticated user who uploads a book, chooses summary length and language, reads the summary and asks questions. | Fast, readable summary; choice of 5 or 15 minutes; Montenegrin or English output; clear status while the book is processed. |
| Student  | Authenticated user who studies a required text with chapter summaries and cited Q&A.                              | Chapter-by-chapter summaries; answers with exact passages; links from key ideas and citations to the chapter.               |
| Guest    | Unauthenticated visitor.                                                                                          | See what the product does on the landing page; register and log in. No access to books.                                     |

# 3. Functional Requirements

## 3.1 Feature List

**Account**

- Register, log in and log out with email and password (FR-001)

**Books and parsing**

- Upload an EPUB or text-based PDF (FR-002)

- Parse EPUB (FR-003) and text-based PDF with clean-up of headers, footers and hyphenation (FR-004)

- Detect chapters and show a clean table of contents (FR-005)

- Library with the user’s private books and the shared public-domain demo library (FR-015)

**Summarisation**

- Chunking that respects chapter and paragraph boundaries (FR-006)

- Chapter notes, generated once per book (FR-007)

- Book summary with selectable length and output language (FR-008)

- Key ideas, each linked to its chapter (FR-009)

**Question answering**

- Embeddings and vector index (FR-010)

- Answers with cited passages (FR-011)

**Operations and quality**

- Processing status (FR-012)

- Token and cost tracking per book (FR-013)

- Caching, so a book is processed only once (FR-014)

- Faithfulness evaluation on public-domain books (FR-016)

- Cost cap per book (FR-017, Should Have)

**Stretch goals (Nice to Have, not planned in the four weeks):** audio summary, flashcards, comparison of two books, OCR for scanned PDFs.

## 3.2 Detailed Requirements

### FR-001 User registration and login

| **Field**        | **Description**                                                                                                                                                                                  |
|------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-001                                                                                                                                                                                           |
| **Title**        | User registration and login                                                                                                                                                                      |
| **Description**  | A visitor can create an account with email and password, log in and log out. All book features require an authenticated user.                                                                    |
| **Priority**     | Must Have (authentication method to be confirmed with the mentor, see Appendix E)                                                                                                                |
| **Input**        | Email, password (minimum 8 characters).                                                                                                                                                          |
| **Processing**   | Email is normalised to lower case and must be unique. The password is hashed with Argon2id and never stored or logged in plain text. On login the API issues a signed JWT in an httpOnly cookie. |
| **Output**       | Account created; session cookie set; user redirected to the library. Wrong credentials return a generic “Invalid email or password” message.                                                     |
| **Dependencies** | None                                                                                                                                                                                             |

### FR-002 Book upload

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-002                                                                                                                                                                                                                                                                                                                                                         |
| **Title**        | Book upload                                                                                                                                                                                                                                                                                                                                                    |
| **Description**  | An authenticated user uploads one book file in EPUB or PDF format.                                                                                                                                                                                                                                                                                             |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                      |
| **Input**        | One file, .epub or .pdf, at most 50 MB.                                                                                                                                                                                                                                                                                                                        |
| **Processing**   | The API checks the extension, the file signature (ZIP for EPUB, %PDF for PDF) and the size. It computes the SHA-256 of the file. If the same user already uploaded the same file, the existing book is returned (FR-014). Otherwise the file is stored under a generated name, a book record is created with status “uploaded” and a processing job is queued. |
| **Output**       | HTTP 202 with the book ID and job ID; the UI shows the book page with processing status.                                                                                                                                                                                                                                                                       |
| **Dependencies** | FR-001, FR-012, FR-014                                                                                                                                                                                                                                                                                                                                         |

### FR-003 EPUB parsing

| **Field**        | **Description**                                                                                                                                                                                                                                                                  |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-003                                                                                                                                                                                                                                                                           |
| **Title**        | EPUB parsing                                                                                                                                                                                                                                                                     |
| **Description**  | The system extracts metadata, reading order and text from an EPUB file.                                                                                                                                                                                                          |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                        |
| **Input**        | Stored EPUB file.                                                                                                                                                                                                                                                                |
| **Processing**   | Read title, author and language from the package metadata. Follow the spine (reading order). Convert each XHTML document to plain paragraphs, dropping scripts, styles, images and navigation. Remove known boilerplate such as the Project Gutenberg licence header and footer. |
| **Output**       | A ParsedBook object: title, author, language and an ordered list of chapters, each with a title and a list of paragraphs.                                                                                                                                                        |
| **Dependencies** | FR-002, FR-005                                                                                                                                                                                                                                                                   |

### FR-004 PDF parsing and clean-up

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-004                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Title**        | PDF parsing and clean-up                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Description**  | The system extracts readable text from a text-based PDF and removes page artefacts.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Input**        | Stored PDF file.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Processing**   | Extract text blocks with position and font size per page. Remove lines that repeat at the top or bottom of most pages (running headers and footers) and page numbers. Join words that are hyphenated across a line break. Rebuild paragraphs from lines. Separate footnotes (small font at the bottom of a page) from the main text; in the MVP they are dropped. If the PDF has almost no extractable text (on average fewer than 100 characters per page) the job fails with the code NO_TEXT_LAYER. |
| **Output**       | The same ParsedBook object as for EPUB, with a page number recorded for each paragraph.                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Dependencies** | FR-002, FR-005                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

### FR-005 Chapter detection and table of contents

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-005                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Title**        | Chapter detection and table of contents                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Description**  | The system splits the book into chapters and shows a clean table of contents.                                                                                                                                                                                                                                                                                                                                                                    |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **Input**        | Parsed text and document structure.                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Processing**   | Use the EPUB navigation document or the PDF outline when present. Otherwise detect headings by pattern (for example “Chapter 7”, “CHAPTER VII”, “Book II”, “Glava 3”) and by layout (short line, larger or bold font, start of page). Front and back matter are kept as separate, unnumbered entries. If no chapters are found, the book is split into parts of about 5,000 words on paragraph boundaries and marked as “chapters not detected”. |
| **Output**       | Ordered chapters stored in the database; a table of contents with chapter title and word count shown in the UI.                                                                                                                                                                                                                                                                                                                                  |
| **Dependencies** | FR-003, FR-004                                                                                                                                                                                                                                                                                                                                                                                                                                   |

### FR-006 Paragraph-aware chunking

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                               |
|------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-006                                                                                                                                                                                                                                                                                                                                                        |
| **Title**        | Paragraph-aware chunking                                                                                                                                                                                                                                                                                                                                      |
| **Description**  | Each chapter is split into chunks used for retrieval and citation.                                                                                                                                                                                                                                                                                            |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                     |
| **Input**        | Chapters with paragraphs.                                                                                                                                                                                                                                                                                                                                     |
| **Processing**   | Paragraphs are grouped in order into chunks with a target of about 350 words (maximum 500). A chunk never crosses a chapter boundary and never cuts a paragraph, except for a single paragraph longer than the maximum, which is split on sentence boundaries. Each chunk stores its chapter, its position, its token count and, for PDFs, its starting page. |
| **Output**       | Chunk records in the database.                                                                                                                                                                                                                                                                                                                                |
| **Dependencies** | FR-005                                                                                                                                                                                                                                                                                                                                                        |

### FR-007 Chapter notes (map step)

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                   |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-007                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Title**        | Chapter notes (map step)                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Description**  | The system produces a detailed, length-independent summary of every chapter once per book.                                                                                                                                                                                                                                                                                                                                        |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Input**        | Text of one chapter.                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Processing**   | One LLM request per chapter, run in parallel with limited concurrency. A chapter that is longer than the input budget (about 12,000 tokens) is split into sections on paragraph boundaries, each section is summarised and the results are merged. The prompt instructs the model to use only information present in the text and to write in the source language of the book. Token usage of every request is recorded (FR-013). |
| **Output**       | Chapter notes (about 150–300 words) stored on the chapter.                                                                                                                                                                                                                                                                                                                                                                        |
| **Dependencies** | FR-005, FR-013, FR-014                                                                                                                                                                                                                                                                                                                                                                                                            |

### FR-008 Book summary with selectable length and language (reduce step)

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-008                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Title**        | Book summary with selectable length and language (reduce step)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Description**  | The user chooses a reading length and an output language and receives a book summary built from the chapter notes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Input**        | Book ID; length: 5 or 15 minutes; language: en or cnr.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Processing**   | If a summary for the same book, length and language exists, it is returned from the cache. Otherwise one LLM request receives the table of contents and all chapter notes and returns a structured result: a one-paragraph overview, a summary for each chapter and the key ideas. The target size is about 1,100 words for 5 minutes and about 3,300 words for 15 minutes (220 words per minute). The output language does not depend on the language of the book; Montenegrin output is requested in Latin script and ijekavian. The result is validated (valid structure, existing chapter references, length within ±15 %); one retry is made if validation fails. |
| **Output**       | Stored summary: overview, chapter summaries and key ideas, shown in the Summary tab.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Dependencies** | FR-007, FR-009, FR-013, FR-014                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

### FR-009 Key ideas linked to chapters

| **Field**        | **Description**                                                                                                                                                                                                                            |
|------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-009                                                                                                                                                                                                                                     |
| **Title**        | Key ideas linked to chapters                                                                                                                                                                                                               |
| **Description**  | The summary contains a list of key ideas and each idea links to the chapter it comes from.                                                                                                                                                 |
| **Priority**     | Must Have                                                                                                                                                                                                                                  |
| **Input**        | Output of the reduce step.                                                                                                                                                                                                                 |
| **Processing**   | Each key idea carries the position of its source chapter. The reference is checked against the chapters of the book; an idea with an invalid reference is discarded. A 5-minute summary has 5–10 key ideas, a 15-minute summary has 10–15. |
| **Output**       | Key ideas shown next to the summary; selecting one opens the chapter in the reader.                                                                                                                                                        |
| **Dependencies** | FR-008                                                                                                                                                                                                                                     |

### FR-010 Embeddings and vector index

| **Field**        | **Description**                                                                                                                                                                                                                                                              |
|------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-010                                                                                                                                                                                                                                                                       |
| **Title**        | Embeddings and vector index                                                                                                                                                                                                                                                  |
| **Description**  | Every chunk is embedded so that passages relevant to a question can be found.                                                                                                                                                                                                |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                    |
| **Input**        | Chunks of a book.                                                                                                                                                                                                                                                            |
| **Processing**   | Chunks are sent to the embedding API in batches. Vectors (1,024 dimensions) are stored in PostgreSQL with pgvector and searched with cosine distance. A multilingual embedding model is used so that a question in Montenegrin can retrieve passages from a book in English. |
| **Output**       | Embedding stored for each chunk; token usage recorded.                                                                                                                                                                                                                       |
| **Dependencies** | FR-006, FR-013                                                                                                                                                                                                                                                               |

### FR-011 Question answering with cited passages

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-011                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Title**        | Question answering with cited passages                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Description**  | The user asks a question about one book and receives an answer that is based only on retrieved passages and shows them as citations.                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Input**        | Book ID; question (1–500 characters); answer language (en or cnr).                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Processing**   | The question is embedded and the 8 most similar chunks of that book are retrieved. The LLM receives the question and the numbered passages and must answer only from them, returning for every statement the passage number and a short verbatim quote that supports it. The backend checks that each quote really occurs in the referenced chunk; statements without a verified citation are removed. If nothing relevant is found, the answer states that the book does not appear to answer the question and no answer is invented. |
| **Output**       | Answer text with numbered citations; each citation shows chapter, passage and the highlighted quote, and links to the passage in the chapter reader. Question, answer and citations are stored.                                                                                                                                                                                                                                                                                                                                        |
| **Dependencies** | FR-010, FR-013                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

### FR-012 Processing status

| **Field**        | **Description**                                                                                                                                                                                                                                                                       |
|------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-012                                                                                                                                                                                                                                                                                |
| **Title**        | Processing status                                                                                                                                                                                                                                                                     |
| **Description**  | The user can see what is happening with a book while it is processed.                                                                                                                                                                                                                 |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                             |
| **Input**        | Book ID or job ID.                                                                                                                                                                                                                                                                    |
| **Processing**   | Each job has a status (queued, running, done, failed), a stage (parsing, chunking, embedding, chapter notes) and a progress percentage. The UI polls the job every 2–3 seconds while it is running. On failure the job stores an error code and a message that a user can understand. |
| **Output**       | Status, stage and progress shown on the book page; clear error message and a retry action on failure.                                                                                                                                                                                 |
| **Dependencies** | FR-002                                                                                                                                                                                                                                                                                |

### FR-013 Token and cost tracking

| **Field**        | **Description**                                                                                                                                                                                                                                                                                 |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-013                                                                                                                                                                                                                                                                                          |
| **Title**        | Token and cost tracking                                                                                                                                                                                                                                                                         |
| **Description**  | The system records tokens used and estimated cost for every book.                                                                                                                                                                                                                               |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                       |
| **Input**        | Usage data returned by the Claude API and the embedding API.                                                                                                                                                                                                                                    |
| **Processing**   | Every API call writes one usage record: book, job, pipeline step, model, input tokens, output tokens and estimated cost. Cost is calculated from a price table kept in configuration (price per million input and output tokens for each model), so prices can be updated without code changes. |
| **Output**       | Per-book totals (tokens and estimated cost in USD), broken down by step, shown in the Cost tab and returned by the API.                                                                                                                                                                         |
| **Dependencies** | FR-007, FR-008, FR-010, FR-011                                                                                                                                                                                                                                                                  |

### FR-014 Result caching

| **Field**        | **Description**                                                                                                                                                                                                                                                                                             |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-014                                                                                                                                                                                                                                                                                                      |
| **Title**        | Result caching                                                                                                                                                                                                                                                                                              |
| **Description**  | A book is processed only once and no LLM result is paid for twice.                                                                                                                                                                                                                                          |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                   |
| **Input**        | Any processing or summary request.                                                                                                                                                                                                                                                                          |
| **Processing**   | Upload is de-duplicated per user by file hash. Every pipeline step stores its result and is skipped when the result already exists: chapters, chunks, embeddings, chapter notes, and summaries (unique per book, length and language). A failed job that is retried continues from the last completed step. |
| **Output**       | Repeated requests return stored results immediately, with no new API cost.                                                                                                                                                                                                                                  |
| **Dependencies** | FR-002, FR-007, FR-008, FR-010                                                                                                                                                                                                                                                                              |

### FR-015 Library and privacy

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                           |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-015                                                                                                                                                                                                                                                                                                                                                    |
| **Title**        | Library and privacy                                                                                                                                                                                                                                                                                                                                       |
| **Description**  | The user sees their own books and the shared demo library; uploaded books are never visible to other users.                                                                                                                                                                                                                                               |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                 |
| **Input**        | Authenticated request.                                                                                                                                                                                                                                                                                                                                    |
| **Processing**   | Every book query is filtered by owner. A book that belongs to another user is reported as not found. Demo books are public-domain titles loaded by a seed script and flagged as demo; they are readable by every authenticated user and cannot be deleted through the API. A user can delete their own book, which removes the file and all derived data. |
| **Output**       | Library page with “My books” and “Demo library”.                                                                                                                                                                                                                                                                                                          |
| **Dependencies** | FR-001                                                                                                                                                                                                                                                                                                                                                    |

### FR-016 Faithfulness evaluation

| **Field**        | **Description**                                                                                                                                                                                                                                                                                                                                                    |
|------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ID**           | FR-016                                                                                                                                                                                                                                                                                                                                                             |
| **Title**        | Faithfulness evaluation                                                                                                                                                                                                                                                                                                                                            |
| **Description**  | The team measures how many summary claims are supported by the source for 2–3 public-domain books.                                                                                                                                                                                                                                                                 |
| **Priority**     | Must Have                                                                                                                                                                                                                                                                                                                                                          |
| **Input**        | Generated summaries of the evaluation books.                                                                                                                                                                                                                                                                                                                       |
| **Processing**   | A script splits each summary into individual claims and exports a random sample (at least 25 claims per book) to a spreadsheet. Team members check each claim against the book and label it supported, partially supported or not supported. Faithfulness rate = supported claims ÷ checked claims. The check is done for the English and the Montenegrin summary. |
| **Output**       | A short evaluation report with the faithfulness rate per book and language, examples of unsupported claims and the cost per book.                                                                                                                                                                                                                                  |
| **Dependencies** | FR-008                                                                                                                                                                                                                                                                                                                                                             |

### FR-017 Cost cap per book

| **Field**        | **Description**                                                                                |
|------------------|------------------------------------------------------------------------------------------------|
| **ID**           | FR-017                                                                                         |
| **Title**        | Cost cap per book                                                                              |
| **Description**  | Processing stops if the estimated cost of a book exceeds a configured limit.                   |
| **Priority**     | Should Have                                                                                    |
| **Input**        | Running job; configured limit (default to be set after the first cost measurements in Week 2). |
| **Processing**   | Before each LLM request the worker compares the cost recorded so far with the limit.           |
| **Output**       | Job fails with the code COST_LIMIT_EXCEEDED; completed steps remain cached.                    |
| **Dependencies** | FR-013                                                                                         |

## 3.3 Use Cases

<table>
<colgroup>
<col style="width: 22%" />
<col style="width: 77%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Element</strong></th>
<th><strong>Description</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>Use Case ID</strong></td>
<td>UC-001</td>
</tr>
<tr class="even">
<td><strong>Title</strong></td>
<td>Upload and process a book</td>
</tr>
<tr class="odd">
<td><strong>Actor(s)</strong></td>
<td>Reader, Student</td>
</tr>
<tr class="even">
<td><strong>Preconditions</strong></td>
<td>The user is logged in and has an EPUB or text-based PDF file of at most 50 MB.</td>
</tr>
<tr class="odd">
<td><strong>Main Flow</strong></td>
<td><p>1. The user opens Upload and selects a file.</p>
<p>2. The system validates the file, stores it and queues a processing job.</p>
<p>3. The system shows the book page with status and progress.</p>
<p>4. The worker parses the book, detects chapters, creates chunks and embeddings and writes chapter notes.</p>
<p>5. The book becomes “ready”; the table of contents is shown.</p></td>
</tr>
<tr class="even">
<td><strong>Alternative Flows</strong></td>
<td><p>2a. The same file was already uploaded by this user: the existing book is opened and nothing is processed again.</p>
<p>4a. No chapters are detected: the book is split into parts and the user is told that chapters could not be detected.</p></td>
</tr>
<tr class="odd">
<td><strong>Postconditions</strong></td>
<td>The book, its chapters, chunks, embeddings and chapter notes are stored; tokens and cost are recorded.</td>
</tr>
<tr class="even">
<td><strong>Exceptions</strong></td>
<td><p>Unsupported type or file too large: rejected before upload completes, with a message.</p>
<p>Scanned PDF without a text layer: job fails with an explanation that OCR is not supported.</p>
<p>Claude or embedding API unavailable: automatic retries, then the job is marked failed and can be retried; completed steps are kept.</p></td>
</tr>
</tbody>
</table>

<table>
<colgroup>
<col style="width: 22%" />
<col style="width: 77%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Element</strong></th>
<th><strong>Description</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>Use Case ID</strong></td>
<td>UC-002</td>
</tr>
<tr class="even">
<td><strong>Title</strong></td>
<td>Generate and read a summary</td>
</tr>
<tr class="odd">
<td><strong>Actor(s)</strong></td>
<td>Reader, Student</td>
</tr>
<tr class="even">
<td><strong>Preconditions</strong></td>
<td>The book is ready.</td>
</tr>
<tr class="odd">
<td><strong>Main Flow</strong></td>
<td><p>1. The user opens the Summary tab and chooses length (5 or 15 minutes) and language (Montenegrin or English).</p>
<p>2. The system generates the summary from the chapter notes and shows progress.</p>
<p>3. The system shows the overview, the chapter summaries and the key ideas.</p>
<p>4. The user selects a key idea and the chapter it comes from opens in the reader.</p></td>
</tr>
<tr class="even">
<td><strong>Alternative Flows</strong></td>
<td><p>2a. A summary with the same length and language already exists: it is shown immediately.</p>
<p>1a. The user switches length or language: the flow repeats for the new combination.</p></td>
</tr>
<tr class="odd">
<td><strong>Postconditions</strong></td>
<td>The summary is stored and will be served from the cache next time.</td>
</tr>
<tr class="even">
<td><strong>Exceptions</strong></td>
<td>The model returns an invalid or too long result: one automatic retry, then a clear error with a retry button.</td>
</tr>
</tbody>
</table>

<table>
<colgroup>
<col style="width: 22%" />
<col style="width: 77%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Element</strong></th>
<th><strong>Description</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>Use Case ID</strong></td>
<td>UC-003</td>
</tr>
<tr class="even">
<td><strong>Title</strong></td>
<td>Ask a question about a book</td>
</tr>
<tr class="odd">
<td><strong>Actor(s)</strong></td>
<td>Reader, Student</td>
</tr>
<tr class="even">
<td><strong>Preconditions</strong></td>
<td>The book is ready (embeddings exist).</td>
</tr>
<tr class="odd">
<td><strong>Main Flow</strong></td>
<td><p>1. The user opens the Ask tab and types a question.</p>
<p>2. The system retrieves the most relevant passages and generates an answer from them.</p>
<p>3. The system shows the answer with numbered citations and the cited passages.</p>
<p>4. The user selects a citation and the passage opens, highlighted, in the chapter reader.</p></td>
</tr>
<tr class="even">
<td><strong>Alternative Flows</strong></td>
<td>2a. The question is in Montenegrin and the book is in English (or the reverse): retrieval still works and the answer is given in the selected language.</td>
</tr>
<tr class="odd">
<td><strong>Postconditions</strong></td>
<td>Question, answer and citations are stored in the question history of the book.</td>
</tr>
<tr class="even">
<td><strong>Exceptions</strong></td>
<td><p>The book does not contain the answer: the system says so and does not invent one.</p>
<p>Empty or too long question: validation message.</p>
<p>API error: message with a retry option; nothing is stored.</p></td>
</tr>
</tbody>
</table>

<table>
<colgroup>
<col style="width: 22%" />
<col style="width: 77%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Element</strong></th>
<th><strong>Description</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>Use Case ID</strong></td>
<td>UC-004</td>
</tr>
<tr class="even">
<td><strong>Title</strong></td>
<td>Register and log in</td>
</tr>
<tr class="odd">
<td><strong>Actor(s)</strong></td>
<td>Guest</td>
</tr>
<tr class="even">
<td><strong>Preconditions</strong></td>
<td>The visitor has no active session.</td>
</tr>
<tr class="odd">
<td><strong>Main Flow</strong></td>
<td><p>1. The visitor opens Register and enters email and password.</p>
<p>2. The system creates the account and logs the user in.</p>
<p>3. The user is taken to the library.</p></td>
</tr>
<tr class="even">
<td><strong>Alternative Flows</strong></td>
<td>1a. The visitor already has an account and uses Log in instead.</td>
</tr>
<tr class="odd">
<td><strong>Postconditions</strong></td>
<td>A session cookie is set; protected pages are available.</td>
</tr>
<tr class="even">
<td><strong>Exceptions</strong></td>
<td><p>Email already registered or password too short: validation message.</p>
<p>Wrong credentials: generic error; repeated attempts are rate-limited.</p></td>
</tr>
</tbody>
</table>

# 4. Non-Functional Requirements

The figures below are initial targets. They will be compared with measurements in Week 2 and Week 3 and corrected here if they prove unrealistic.

## 4.1 Performance

- API requests that do not call an LLM answer in under 500 ms (95th percentile).

- Upload is acknowledged (HTTP 202) within 3 seconds for a 50 MB file on a normal connection; processing always runs in the background.

- Parsing and chapter detection of a 300-page book finish in under 60 seconds.

- Full first-time processing of a typical book (about 100,000 words) finishes in under 10 minutes; chapter notes are generated with up to 5 parallel requests.

- A cached summary is returned in under 1 second; a new summary from existing chapter notes in under 2 minutes.

- An answer to a question is returned in under 15 seconds (95th percentile).

- Cost per book is measured and reported; a target value will be set after the first measurements.

## 4.2 Security

- **Authentication:** email and password; passwords hashed with Argon2id; JWT (HS256, 24-hour lifetime) in an httpOnly, Secure, SameSite=Lax cookie.

- **Authorisation:** ownership check on every book, summary, question and job request. Resources of other users return 404. Demo books are read-only.

- **Input validation:** all request bodies validated with Pydantic schemas; uploads checked by extension, file signature and size; files stored outside the web root under generated names.

- **Injection:** database access only through the ORM with bound parameters; summaries and answers rendered as text, never as raw HTML.

- **Prompt injection:** book text is untrusted input. It is passed to the model inside clearly delimited blocks and the system prompt instructs the model to treat it as content to summarise, never as instructions.

- **Secrets:** API keys and the JWT secret only in environment variables; .env is git-ignored and .env.example documents the variables. No secrets, book text or personal data in logs.

- **Transport:** HTTPS in the deployed environment; CORS limited to the application origin.

- **Abuse limits:** rate limits on login, upload and question endpoints.

- **Privacy and copyright:** uploaded books are processed only for the uploading user and are never shared; the demo library contains only public-domain books.

## 4.3 Scalability

The MVP targets a demo with a small number of concurrent users on a single host. The design still allows growth: the API is stateless, so more API containers can be added behind the proxy; long work runs in worker processes that can be scaled independently of the API; worker concurrency and the number of parallel LLM requests are configuration values; pgvector uses an HNSW index so search stays fast as the number of chunks grows; and caching of every pipeline step keeps cost and load proportional to new books only.

## 4.4 Usability

- Responsive layout usable from 360 px (phone) to desktop; main breakpoints at 640, 768 and 1024 px.

- Supported browsers: current versions of Chrome, Firefox, Edge and Safari.

- Interface language: English in the MVP. Summary and answer language: English or Montenegrin, selected by the user.

- Accessibility basics following WCAG 2.1 AA: sufficient contrast, keyboard navigation, visible focus, labels on all form fields.

- Every long operation shows status and progress; every error says what happened and what the user can do next.

- The main flow (upload → summary → question) needs no instructions.

## 4.5 Reliability and Availability

- Calls to external APIs are retried up to 3 times with exponential back-off on rate limits, timeouts and server errors.

- Pipeline steps are idempotent; a retried job continues from the last completed step and never duplicates data or cost.

- A failed job never leaves a book in “processing”: the book is marked failed with a reason and can be retried.

- The worker and API restart automatically (container restart policy). Queued jobs survive an API restart.

- No formal uptime target for the MVP; the deployed application must be available for the mentor check-ins and the final demo.

- Daily database dump on the demo host; uploaded files are kept on a persistent volume.

## 4.6 Maintainability

- **Code style:** Python with type hints, formatted and linted with Ruff; TypeScript in strict mode with ESLint and Prettier.

- **Structure:** backend layered into routers, services and data access; parsing and pipeline code are plain Python modules that can be tested without the web framework. Prompts are kept in versioned files, not inside functions.

- **Version control:** GitHub (https://github.com/DinoDj123/bookcrunchai); main is always runnable; work happens on short feature branches (feat/…, fix/…) merged by pull request after review by the other member; small, meaningful commits from both members.

- **CI:** GitHub Actions runs linting and tests on every pull request.

- **Documentation:** README (how to run locally, architecture overview, known limitations); this specification kept up to date; API documentation generated by FastAPI (OpenAPI).

- **Configuration:** models, prices, limits and concurrency in environment variables or one settings module.

# 5. System Architecture

## 5.1 Architecture Overview

bookcrunch.ai is a client–server web application with a **modular monolith** on the back end and a separate **background worker**. The Next.js front end talks to one FastAPI service over a JSON REST API. Work that takes longer than a normal request (parsing, embedding, summarisation) is placed on a job queue and executed by a worker process that shares the same code base and database as the API. Inside the back end the code is layered: routers (HTTP), services (business rules and pipeline steps) and data access (ORM models).

This pattern was chosen because the team has two developers and four weeks. One deployable code base is quick to build, test and explain, while the queue and worker give the reliability that long LLM pipelines need: an upload returns immediately, progress is visible, and a failed step can be retried without repeating finished work. Microservices were rejected as unnecessary overhead for this size of system. Running the pipeline inside the web request was rejected because processing a book takes minutes and would be lost on a timeout or restart.

## 5.2 Architecture Diagram

*(Diagram in the Word version of the specification.)*

*Figure 1. High-level architecture*

*(Diagram in the Word version of the specification.)*

*Figure 2. Processing pipeline*

## 5.3 Component Description

| **Component**    | **Responsibility**                                                                                                                                             | **Technology**                                        |
|------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|
| Frontend         | Pages for login, library, upload, book (summary, ask, contents, cost) and chapter reader; polling of job status; rendering of citations and links to passages. | Next.js (App Router), React, TypeScript, Tailwind CSS |
| Backend API      | Authentication, validation, ownership checks, book and summary endpoints, question answering, queuing of jobs, OpenAPI documentation.                          | Python, FastAPI, Pydantic, SQLAlchemy                 |
| Worker           | Runs the pipeline: parse → chunk → embed → chapter notes, and summary generation; retries; progress updates; usage records.                                    | Python, ARQ                                           |
| Parsing module   | EPUB and PDF text extraction, clean-up and chapter detection; returns a ParsedBook. No dependency on the web framework or the database.                        | zipfile, BeautifulSoup (lxml), PyMuPDF                |
| LLM client       | One wrapper for all Claude API calls: prompts, retries, structured output validation, recording of tokens and cost.                                            | Anthropic Python SDK                                  |
| Retrieval module | Embedding of chunks and questions, vector search, citation verification.                                                                                       | Voyage AI API, pgvector                               |
| Database         | Users, books, chapters, chunks with embeddings, summaries, questions, citations, jobs and usage.                                                               | PostgreSQL with pgvector                              |
| Job queue        | Holds queued jobs between the API and the worker.                                                                                                              | Redis                                                 |
| File storage     | Uploaded book files, private per user.                                                                                                                         | Local disk volume                                     |
| Reverse proxy    | HTTPS termination; routes page requests to the frontend and /api requests to the backend, so both share one origin.                                            | Caddy                                                 |

## 5.4 Communication and Integration

- **Browser ↔ backend:** REST over HTTPS, JSON bodies, base path /api/v1. File upload uses multipart/form-data. The session JWT travels in an httpOnly cookie; because the proxy serves the frontend and the API from the same origin, no cross-site cookie configuration is needed.

- **Status updates:** the frontend polls the job endpoint every 2–3 seconds while a job is running. WebSockets and server-sent events were considered and rejected for the MVP because polling is simpler and sufficient.

- **API ↔ worker:** the API writes a job row to PostgreSQL and enqueues the job ID in Redis; the worker reads its input from the database and writes results and progress back to it. PostgreSQL is the single source of truth for job status.

- **Claude API:** HTTPS through the official SDK. Structured results (summary, answer with citations) are requested as JSON and validated with Pydantic before they are stored.

- **Embedding API:** HTTPS, batched requests for chunks, a single request for a question.

- **Parser contract:** parse_book(file_path) → ParsedBook. The worker depends only on this function, which lets parsing and platform work proceed in parallel.

## 5.5 Deployment Architecture

All services run as Docker containers defined in one Docker Compose file: proxy, frontend, api, worker, postgres (with pgvector) and redis. The same file is used for local development and for the demo host, with different environment files.

- **Environments:** local development on each developer’s machine, and one production (demo) environment. A separate staging environment is not planned.

- **Hosting:** one Linux virtual machine. The provider is an open question for the mentor (Appendix E).

- **CI/CD:** GitHub Actions runs linting and tests on every pull request. Deployment in the MVP is a documented manual step on the host (pull, build, migrate, restart); automatic deployment is optional.

- **Database migrations:** Alembic, executed as part of the deployment step.

- **Early deployment:** the skeleton is deployed by the end of Week 2 so that deployment problems are found long before the demo.

# 6. Data Model

## 6.1 Entity-Relationship Diagram

*(Diagram in the Word version of the specification.)*

*Figure 3. Entity-relationship diagram (crow’s foot marks the “many” side)*

## 6.2 Entity Descriptions

All primary keys are UUIDs generated by the application. All tables have a created_at timestamp with a default of the current time; it is listed only once below. Columns are NOT NULL unless marked NULL. Rows that link two things of one book (chunks, chapter summaries, key ideas, citations) store book_id and reference those things with composite foreign keys, for example (chapter_id, book_id) → chapters (id, book_id), so the database refuses a link to another book; chapters, chunks, summaries and questions therefore have UNIQUE (id, book_id). All these foreign keys cascade on delete, so that deleting a book removes everything derived from it (Section 6.3). The implementation is backend/app/db/models.py; the schema is created by Alembic migrations.

### users

A registered user.

| **Attribute** | **Type**     | **Constraints**  | **Description**                   |
|---------------|--------------|------------------|-----------------------------------|
| id            | UUID         | PK               | Unique identifier                 |
| email         | VARCHAR(255) | UNIQUE, NOT NULL, CHECK lower case | Login email, stored in lower case |
| password_hash | VARCHAR(255) | NOT NULL         | Argon2id hash of the password     |
| created_at    | TIMESTAMPTZ  | DEFAULT NOW()    | Record creation time              |

### books

An uploaded book or a demo book.

| **Attribute**     | **Type**     | **Constraints**                | **Description**                            |
|-------------------|--------------|--------------------------------|--------------------------------------------|
| id                | UUID         | PK                             | Unique identifier                          |
| owner_id          | UUID         | FK → users.id, NOT NULL        | Uploading user (seed user for demo books)  |
| title             | VARCHAR(500) | NOT NULL                       | From metadata, or the uploaded file name if missing |
| author            | VARCHAR(300) | NULL                           | From metadata                              |
| source_language   | VARCHAR(8)   | NULL                           | Detected language of the book              |
| file_format       | VARCHAR(4)   | NOT NULL, epub \| pdf          | Format of the uploaded file                |
| file_sha256       | CHAR(64)     | NOT NULL, UNIQUE with owner_id | Hash of the file, used for de-duplication  |
| file_path         | VARCHAR(500) | NOT NULL                       | Generated name of the stored file, relative to the storage directory |
| file_size_bytes   | BIGINT       | NOT NULL, \> 0                 | File size                                  |
| status            | VARCHAR(12)  | NOT NULL                       | uploaded \| processing \| ready \| failed  |
| error_code        | VARCHAR(40)  | NULL                           | Reason when status is failed               |
| chapters_detected | BOOLEAN      | NOT NULL, DEFAULT TRUE         | False when the book was split into parts   |
| word_count        | INTEGER      | NULL                           | Total words after clean-up                 |
| is_demo           | BOOLEAN      | NOT NULL, DEFAULT FALSE        | True for shared public-domain books        |

### chapters

A chapter (or part) of a book, in reading order.

| **Attribute** | **Type**     | **Constraints**                  | **Description**                        |
|---------------|--------------|----------------------------------|----------------------------------------|
| id            | UUID         | PK                               | Unique identifier                      |
| book_id       | UUID         | FK → books.id, ON DELETE CASCADE | Owning book                            |
| position      | INTEGER      | NOT NULL, UNIQUE with book_id    | Order in the book, starting at 1       |
| title         | VARCHAR(500) | NOT NULL                         | Chapter title                          |
| kind          | VARCHAR(12)  | NOT NULL                         | chapter \| front_matter \| back_matter |
| word_count    | INTEGER      | NOT NULL                         | Words in the chapter                   |
| notes         | TEXT         | NULL                             | Chapter notes from the map step        |

### chunks

A passage of whole paragraphs from one chapter; the unit of retrieval and citation.

| **Attribute** | **Type**     | **Constraints**                     | **Description**                         |
|---------------|--------------|-------------------------------------|-----------------------------------------|
| id            | UUID         | PK                                  | Unique identifier                       |
| book_id       | UUID         | FK → books.id, ON DELETE CASCADE    | Owning book (stored for fast filtering) |
| chapter_id    | UUID         | FK (chapter_id, book_id) → chapters (id, book_id), ON DELETE CASCADE | Owning chapter of the same book |
| position      | INTEGER      | NOT NULL, UNIQUE with chapter_id    | Order inside the chapter                |
| text          | TEXT         | NOT NULL                            | Passage text                            |
| token_count   | INTEGER      | NOT NULL                            | Size of the passage in tokens           |
| page_start    | INTEGER      | NULL                                | First page (PDF only)                   |
| embedding     | VECTOR(1024) | NULL, HNSW index (cosine)           | Embedding of the text                   |

### summaries

One summary of a book for a given length and language (the cache key).

| **Attribute**               | **Type**    | **Constraints**                  | **Description**                        |
|-----------------------------|-------------|----------------------------------|----------------------------------------|
| id                          | UUID        | PK                               | Unique identifier                      |
| book_id                     | UUID        | FK → books.id, ON DELETE CASCADE | Summarised book                        |
| length                      | VARCHAR(8)  | NOT NULL, 5min \| 15min          | Reading length                         |
| language                    | VARCHAR(8)  | NOT NULL, en \| cnr              | Output language                        |
| status                      | VARCHAR(12) | NOT NULL                         | queued \| running \| ready \| failed   |
| overview                    | TEXT        | NULL                             | One-paragraph overview                 |
| word_count                  | INTEGER     | NULL                             | Total words of the summary             |
| model                       | VARCHAR(60) | NULL                             | Model that produced it                 |
| (book_id, length, language) | –           | UNIQUE                           | Guarantees one summary per combination |

### chapter_summaries

The summary of one chapter inside a book summary.

| **Attribute** | **Type** | **Constraints**                      | **Description**                        |
|---------------|----------|--------------------------------------|----------------------------------------|
| id            | UUID     | PK                                   | Unique identifier                      |
| book_id | UUID | NOT NULL | Book of the summary and the chapter |
| summary_id | UUID | FK (summary_id, book_id) → summaries (id, book_id), ON DELETE CASCADE | Parent summary |
| chapter_id | UUID | FK (chapter_id, book_id) → chapters (id, book_id), ON DELETE CASCADE | Summarised chapter |
| content       | TEXT     | NOT NULL                             | Chapter summary in the output language |

### key_ideas

A key idea of the book, linked to the chapter it comes from.

| **Attribute** | **Type** | **Constraints**                      | **Description**   |
|---------------|----------|--------------------------------------|-------------------|
| id            | UUID     | PK                                   | Unique identifier |
| book_id | UUID | NOT NULL | Book of the summary and the chapter |
| summary_id | UUID | FK (summary_id, book_id) → summaries (id, book_id), ON DELETE CASCADE | Parent summary |
| chapter_id | UUID | FK (chapter_id, book_id) → chapters (id, book_id), ON DELETE CASCADE | Source chapter |
| position      | INTEGER  | NOT NULL                             | Order in the list |
| text          | TEXT     | NOT NULL                             | The idea          |

### questions

A question asked about a book, with its answer.

| **Attribute** | **Type**     | **Constraints**                  | **Description**                                  |
|---------------|--------------|----------------------------------|--------------------------------------------------|
| id            | UUID         | PK                               | Unique identifier                                |
| book_id       | UUID         | FK → books.id, ON DELETE CASCADE | Book the question is about                       |
| user_id       | UUID         | FK → users.id                    | User who asked                                   |
| question      | VARCHAR(500) | NOT NULL                         | Question text                                    |
| answer        | TEXT         | NOT NULL                         | Answer text with citation markers                |
| language      | VARCHAR(8)   | NOT NULL, en \| cnr              | Answer language                                  |
| answerable    | BOOLEAN      | NOT NULL                         | False when the book does not answer the question |

### citations

A verified link from an answer to a chunk.

| **Attribute** | **Type** | **Constraints**                      | **Description**                     |
|---------------|----------|--------------------------------------|-------------------------------------|
| id            | UUID     | PK                                   | Unique identifier                   |
| book_id | UUID | NOT NULL | Book the question is about |
| question_id | UUID | FK (question_id, book_id) → questions (id, book_id), ON DELETE CASCADE | Answer that cites |
| chunk_id | UUID | FK (chunk_id, book_id) → chunks (id, book_id), ON DELETE CASCADE | Cited passage |
| position      | INTEGER  | NOT NULL                             | Citation number shown in the answer |
| quote         | TEXT     | NOT NULL                             | Verbatim quote found in the chunk   |

### jobs

A background job.

| **Attribute**             | **Type**      | **Constraints**                  | **Description**                                             |
|---------------------------|---------------|----------------------------------|-------------------------------------------------------------|
| id                        | UUID          | PK                               | Unique identifier                                           |
| book_id                   | UUID          | FK → books.id, ON DELETE CASCADE | Book being processed                                        |
| type                      | VARCHAR(24)   | NOT NULL                         | process_book \| generate_summary                            |
| status                    | VARCHAR(12)   | NOT NULL                         | queued \| running \| done \| failed                         |
| stage                     | VARCHAR(24)   | NULL                             | parsing \| chunking \| embedding \| chapter_notes \| reduce |
| progress                  | SMALLINT      | NOT NULL, 0–100                  | Progress percentage                                         |
| params                    | JSONB         | NULL                             | For example length and language                             |
| error_code, error_message | VARCHAR, TEXT | NULL                             | Failure reason                                              |
| attempts                  | SMALLINT      | NOT NULL, DEFAULT 0              | Number of attempts                                          |
| started_at, finished_at   | TIMESTAMPTZ   | NULL                             | Timing                                                      |

### llm_usage

One record per external API call; the basis for cost reporting.

| **Attribute** | **Type**      | **Constraints**                  | **Description**                         |
|---------------|---------------|----------------------------------|-----------------------------------------|
| id            | UUID          | PK                               | Unique identifier                       |
| book_id       | UUID          | FK → books.id, ON DELETE CASCADE | Book the call belongs to                |
| job_id        | UUID          | FK → jobs.id, NULL, ON DELETE SET NULL | Job, if the call was made by the worker |
| step          | VARCHAR(16)   | NOT NULL                         | map \| reduce \| embed \| qa            |
| model         | VARCHAR(60)   | NOT NULL                         | Model identifier                        |
| input_tokens  | INTEGER       | NOT NULL, ≥ 0                    | Tokens sent                             |
| output_tokens | INTEGER       | NOT NULL, ≥ 0                    | Tokens received                         |
| cost_usd      | NUMERIC(10,6) | NOT NULL, ≥ 0                    | Estimated cost from the price table     |

## 6.3 Data Validation Rules

- Email: valid format, at most 255 characters, unique, stored in lower case (also a database CHECK constraint). Password: 8–128 characters.

- Upload: extension .epub or .pdf, matching file signature, size from 1 byte to 50 MB.

- A user cannot have two books with the same file hash (unique owner_id + file_sha256).

- Book status, summary status, job status, job type, length and language accept only the listed values (enforced in the application and by database CHECK constraints).

- Chapter position is unique within a book; chunk position is unique within a chapter.

- A chunk always belongs to exactly one chapter of the same book.

- Only one summary exists per book, length and language.

- A chapter summary and a key idea must reference a summary and a chapter of the same book, and a citation must reference a chunk of the book the question is about (enforced by composite foreign keys).

- A citation is stored only if its quote is found in the referenced chunk (after whitespace normalisation).

- Question text: 1–500 characters after trimming.

- Token counts and cost are never negative; progress is between 0 and 100.

- Deleting a book deletes all derived rows (cascade) and the stored file.

# 7. API Specification

## 7.1 API Design Principles

- REST over HTTPS with JSON; resource names are plural nouns in lower case.

- All endpoints are versioned under /api/v1.

- Long operations return 202 Accepted with a job that the client polls; cached results return 200 immediately.

- Standard status codes: 200, 201, 202, 204, 400, 401, 404, 409, 413, 415, 422, 429, 500, 503.

- One error format for all endpoints: { "error": { "code": "BOOK_NOT_FOUND", "message": "…", "details": { } } }. Codes are stable strings the frontend can rely on.

- Identifiers are UUIDs; timestamps are ISO 8601 in UTC.

- The OpenAPI specification is generated by FastAPI and available at /api/v1/docs; it is the detailed reference for request and response schemas.

## 7.2 Endpoint Documentation

Overview of all endpoints:

| **Method and path**                        | **Purpose**                                                | **Auth** |
|--------------------------------------------|------------------------------------------------------------|----------|
| POST /api/v1/auth/register                 | Create an account and start a session                      | No       |
| POST /api/v1/auth/login                    | Log in                                                     | No       |
| POST /api/v1/auth/logout                   | End the session                                            | Yes      |
| GET /api/v1/auth/me                        | Current user                                               | Yes      |
| POST /api/v1/books                         | Upload a book and queue processing                         | Yes      |
| GET /api/v1/books                          | List own books and demo books                              | Yes      |
| GET /api/v1/books/{id}                     | Book details, status and cost totals                       | Yes      |
| DELETE /api/v1/books/{id}                  | Delete an own book and all derived data                    | Yes      |
| POST /api/v1/books/{id}/retry              | Retry failed processing                                    | Yes      |
| GET /api/v1/books/{id}/chapters            | Table of contents                                          | Yes      |
| GET /api/v1/books/{id}/chapters/{position} | Chapter text as a list of chunks (reader, citation target) | Yes      |
| POST /api/v1/books/{id}/summaries          | Get or generate a summary for a length and language        | Yes      |
| GET /api/v1/books/{id}/summaries           | Read a summary (query: length, language)                   | Yes      |
| POST /api/v1/books/{id}/questions          | Ask a question; returns the answer with citations          | Yes      |
| GET /api/v1/books/{id}/questions           | Question history of the book                               | Yes      |
| GET /api/v1/books/{id}/usage               | Tokens and estimated cost by step                          | Yes      |
| GET /api/v1/jobs/{id}                      | Job status, stage and progress                             | Yes      |
| GET /api/v1/health                         | Health check                                               | No       |

Details of the main endpoints:

| **Field**            | **Value**                                                       |
|----------------------|-----------------------------------------------------------------|
| **Endpoint**         | POST /api/v1/auth/login                                         |
| **Description**      | Log in with email and password.                                 |
| **Auth Required**    | No                                                              |
| **Path Parameters**  | None                                                            |
| **Query Parameters** | None                                                            |
| **Request Body**     | { email: string, password: string }                             |
| **Success Response** | 200 OK – { id, email }; sets the session cookie                 |
| **Error Responses**  | 401 INVALID_CREDENTIALS, 422 VALIDATION_ERROR, 429 RATE_LIMITED |

<table>
<colgroup>
<col style="width: 22%" />
<col style="width: 77%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Field</strong></th>
<th><strong>Value</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>Endpoint</strong></td>
<td>POST /api/v1/books</td>
</tr>
<tr class="even">
<td><strong>Description</strong></td>
<td>Upload a book file and queue processing.</td>
</tr>
<tr class="odd">
<td><strong>Auth Required</strong></td>
<td>Yes – session cookie</td>
</tr>
<tr class="even">
<td><strong>Path Parameters</strong></td>
<td>None</td>
</tr>
<tr class="odd">
<td><strong>Query Parameters</strong></td>
<td>None</td>
</tr>
<tr class="even">
<td><strong>Request Body</strong></td>
<td>multipart/form-data with one field: file (.epub or .pdf, at most 50 MB)</td>
</tr>
<tr class="odd">
<td><strong>Success Response</strong></td>
<td><p>202 Accepted – { book: { id, title, status }, job: { id, status } }</p>
<p>200 OK with the existing book if this user already uploaded the same file</p></td>
</tr>
<tr class="even">
<td><strong>Error Responses</strong></td>
<td>401 UNAUTHENTICATED, 413 FILE_TOO_LARGE, 415 UNSUPPORTED_FILE_TYPE, 422 VALIDATION_ERROR, 503 QUEUE_UNAVAILABLE (the job queue cannot be reached; nothing is stored)</td>
</tr>
</tbody>
</table>

| **Field**            | **Value**                                                                                                                                                                                             |
|----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Endpoint**         | GET /api/v1/books/{id}                                                                                                                                                                                |
| **Description**      | Retrieve one book with processing status and cost totals.                                                                                                                                             |
| **Auth Required**    | Yes – session cookie                                                                                                                                                                                  |
| **Path Parameters**  | id: UUID – book ID                                                                                                                                                                                    |
| **Query Parameters** | None                                                                                                                                                                                                  |
| **Request Body**     | N/A                                                                                                                                                                                                   |
| **Success Response** | 200 OK – { id, title, author, source_language, file_format, status, error_code, chapters_detected, chapter_count, word_count, is_demo, active_job, usage: { input_tokens, output_tokens, cost_usd } } |
| **Error Responses**  | 401 UNAUTHENTICATED, 404 BOOK_NOT_FOUND (also for books of other users)                                                                                                                               |

<table>
<colgroup>
<col style="width: 22%" />
<col style="width: 77%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Field</strong></th>
<th><strong>Value</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>Endpoint</strong></td>
<td>POST /api/v1/books/{id}/summaries</td>
</tr>
<tr class="even">
<td><strong>Description</strong></td>
<td>Return the summary for the given length and language, or queue its generation.</td>
</tr>
<tr class="odd">
<td><strong>Auth Required</strong></td>
<td>Yes – session cookie</td>
</tr>
<tr class="even">
<td><strong>Path Parameters</strong></td>
<td>id: UUID – book ID</td>
</tr>
<tr class="odd">
<td><strong>Query Parameters</strong></td>
<td>None</td>
</tr>
<tr class="even">
<td><strong>Request Body</strong></td>
<td>{ length: "5min" | "15min", language: "en" | "cnr" }</td>
</tr>
<tr class="odd">
<td><strong>Success Response</strong></td>
<td><p>200 OK – { id, status: "ready", length, language, overview, chapters: [ { chapter_position, title, content } ], key_ideas: [ { text, chapter_position } ], word_count } when cached</p>
<p>202 Accepted – { summary: { id, status }, job: { id, status } } when generation was queued</p></td>
</tr>
<tr class="even">
<td><strong>Error Responses</strong></td>
<td>401 UNAUTHENTICATED, 404 BOOK_NOT_FOUND, 409 BOOK_NOT_READY, 422 VALIDATION_ERROR</td>
</tr>
</tbody>
</table>

| **Field**            | **Value**                                                                                                                                  |
|----------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| **Endpoint**         | POST /api/v1/books/{id}/questions                                                                                                          |
| **Description**      | Ask a question about the book and receive a grounded answer.                                                                               |
| **Auth Required**    | Yes – session cookie                                                                                                                       |
| **Path Parameters**  | id: UUID – book ID                                                                                                                         |
| **Query Parameters** | None                                                                                                                                       |
| **Request Body**     | { question: string (1–500 characters), language: "en" \| "cnr" }                                                                           |
| **Success Response** | 200 OK – { id, question, answer, answerable, citations: \[ { position, chunk_id, chapter_position, chapter_title, quote, page_start } \] } |
| **Error Responses**  | 401 UNAUTHENTICATED, 404 BOOK_NOT_FOUND, 409 BOOK_NOT_READY, 422 VALIDATION_ERROR, 429 RATE_LIMITED, 502 LLM_UNAVAILABLE                   |

| **Field**            | **Value**                                                                                                   |
|----------------------|-------------------------------------------------------------------------------------------------------------|
| **Endpoint**         | GET /api/v1/jobs/{id}                                                                                       |
| **Description**      | Read the status of a background job.                                                                        |
| **Auth Required**    | Yes – session cookie                                                                                        |
| **Path Parameters**  | id: UUID – job ID                                                                                           |
| **Query Parameters** | None                                                                                                        |
| **Request Body**     | N/A                                                                                                         |
| **Success Response** | 200 OK – { id, book_id, type, status, stage, progress, error_code, error_message, started_at, finished_at } |
| **Error Responses**  | 401 UNAUTHENTICATED, 404 JOB_NOT_FOUND                                                                      |

## 7.3 Authentication and Authorization

**Status: proposed, to be confirmed with the mentor (Appendix E).**

1.  Registration: POST /auth/register validates the input (email, password of 8 to 128 characters), stores the user with an Argon2id password hash and logs the user in: 201 Created with { id, email } and the session cookie; 409 EMAIL_TAKEN if the email is already registered.

2.  Login: POST /auth/login verifies the password and issues a JWT signed with HS256 that contains the user ID and an expiry time 24 hours ahead.

3.  The token is returned in an httpOnly, Secure, SameSite=Lax cookie named session, so page scripts cannot read it. Only in local development, which runs on plain HTTP, the cookie is set without Secure. Outside local development (ENVIRONMENT other than local, or a site address with a domain name) the API does not start with a default or short JWT secret.

4.  Every protected endpoint uses one FastAPI dependency that reads the cookie, verifies signature and expiry, and loads the user. A missing or invalid token gives 401.

5.  Logout: POST /auth/logout clears the cookie.

6.  There is no refresh token in the MVP; after 24 hours the user logs in again. This is a known limitation.

Authorisation is ownership-based, not role-based: a user can access a book, and everything derived from it, only if they own it or it is a demo book. Demo books cannot be deleted or re-processed through the API. Requests for resources of other users receive 404 so that their existence is not revealed.

# 8. User Interface Design

## 8.1 Wireframes

Low-fidelity wireframes of the key screens. Registration uses the same layout as login. The Contents tab is a list of chapters, and the chapter reader is a single text column in which the cited passage is highlighted.

*(Diagram in the Word version of the specification.)*

*Figure 4. Log in*

*(Diagram in the Word version of the specification.)*

*Figure 5. Library: private books and demo library*

*(Diagram in the Word version of the specification.)*

*Figure 6. Upload and processing status*

*(Diagram in the Word version of the specification.)*

*Figure 7. Book page, Summary tab*

*(Diagram in the Word version of the specification.)*

*Figure 8. Book page, Ask tab with cited passages*

## 8.2 Navigation Flow

Screens marked \[auth\] require a logged-in user; an unauthenticated request is redirected to the login page.

*(Diagram in the Word version of the specification.)*

*Figure 9. Navigation flow*

## 8.3 Design Guidelines

- **Principle:** a calm reading interface. Text first, few colours, generous spacing.

- **Component library:** Tailwind CSS with shadcn/ui components (buttons, tabs, dialogs, toasts).

- **Colours:** background \#FFFFFF, surface \#F4F6FA, text \#2B3550, muted text \#8A93A8, accent \#2F5BD3 (actions, links, citations), success \#2E8B57, warning \#B7791F, error \#C0392B.

- **Typography:** a system sans-serif for the interface; summaries and book text at 17–18 px with a line height of 1.6 and a maximum line length of about 70 characters.

- **Spacing:** 4 px base unit (Tailwind scale); cards with 8 px corner radius.

- **Citations:** always shown as numbered markers in the accent colour; the same number identifies the passage in the side panel and in the reader.

- **Feedback:** progress indicators for jobs, skeletons while loading, toasts for errors with a retry action.

# 9. Technology Stack

The stack follows the one suggested in the project brief. Versions are the major versions planned at the time of writing; exact versions are pinned in the lock files of the repository.

| **Layer**          | **Technology**                            | **Version**       | **Justification**                                                                                                                                   |
|--------------------|-------------------------------------------|-------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Frontend           | Next.js (App Router) + React + TypeScript | 16.x / 19.x / 5.x | Suggested in the brief; file-based routing; large ecosystem; type safety.                                                                           |
| Styling            | Tailwind CSS + shadcn/ui                  | 4.x               | Fast, consistent UI without writing a design system.                                                                                                |
| Backend            | Python + FastAPI                          | 3.12 / 0.115+     | Suggested in the brief; best library support for parsing and LLM work; automatic OpenAPI docs; async I/O.                                           |
| Validation         | Pydantic                                  | 2.x               | Request validation and validation of structured LLM output.                                                                                         |
| Database           | PostgreSQL + pgvector                     | 17 / 0.8          | Relational data and vector search in one database; one service fewer than a dedicated vector store.                                                 |
| ORM and migrations | SQLAlchemy + Alembic                      | 2.x / 1.x         | Standard for FastAPI; versioned schema migrations.                                                                                                  |
| Job queue          | ARQ + Redis                               | 0.26+ / 7.x       | Small async queue with retries; fits async FastAPI and the async Claude client.                                                                     |
| EPUB parsing       | zipfile + BeautifulSoup + lxml            | – / 4.x / 5.x     | Reads the package (OPF), spine and navigation from the archive; XHTML to clean text. EbookLib is not used (Appendix D).                             |
| PDF parsing        | PyMuPDF                                   | 1.24+             | Text with position and font size, needed to remove headers and footers and to detect headings. Licence note in Appendix E.                          |
| LLM                | Claude API (Anthropic Python SDK)         | current           | Required by the brief. A smaller, cheaper model for chapter notes and a stronger model for the book summary and Q&A; model names are configuration. |
| Embeddings         | Voyage AI (voyage-4 family)               | 1,024 dimensions  | Multilingual, so Montenegrin questions work on English books; Anthropic does not provide an embedding model.                                        |
| Auth               | JWT (PyJWT) + Argon2id                    | –                 | Stateless authentication; modern password hashing. To be confirmed with the mentor.                                                                 |
| Testing            | pytest, Vitest, Playwright                | –                 | Unit and integration tests for the backend; component tests; one end-to-end happy path.                                                             |
| Code quality       | Ruff, ESLint, Prettier                    | –                 | Consistent style, checked in CI.                                                                                                                    |
| DevOps             | Docker Compose, GitHub Actions, Caddy     | –                 | Same setup locally and on the demo host; CI on every pull request; automatic HTTPS.                                                                 |
| Version Control    | Git + GitHub                              | –                 | Feature branches, pull requests and review.                                                                                                         |

# 10. Testing Strategy

## 10.1 Testing Levels

| **Level**               | **Scope**                                                                                                                         | **Tools**                                             | **Responsibility**                        |
|-------------------------|-----------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|-------------------------------------------|
| Unit Tests              | Critical logic: EPUB and PDF clean-up, chapter detection, chunking, cost calculation, citation verification, summary validation.  | pytest; Vitest for frontend helpers                   | The developer who writes the module       |
| Integration             | API endpoints with a test database; full pipeline with the Claude and embedding APIs replaced by fakes; ownership rules; caching. | pytest, FastAPI TestClient, test PostgreSQL in Docker | Both developers, each for their endpoints |
| E2E Tests               | One happy path through the UI: log in → upload → summary → question → open citation.                                              | Playwright                                            | Daris Velović (written in Week 3–4)       |
| Manual                  | Exploratory testing of the UI; checking Montenegrin output; processing of the three test books with real API calls.               | Checklist in the repository                           | Both developers                           |
| Faithfulness evaluation | Manual check of sampled summary claims against the source (FR-016).                                                               | Sampling script, spreadsheet                          | Dino Đerekerac leads, both label          |

Automated tests never call the paid APIs. Real API calls are made only in manual runs and in the evaluation, and their cost is recorded.

**Test books (public domain):**

| **\#** | **Book**                                                       | **Format and source**                    | **Why it was chosen**                                                                                                                                   |
|--------|----------------------------------------------------------------|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1      | Pride and Prejudice, Jane Austen                               | EPUB, Project Gutenberg (ebook no. 1342) | Clean case: 61 numbered chapters and a proper table of contents. Also the main book for the demo.                                                       |
| 2      | The Prince, Niccolò Machiavelli (translated by W. K. Marriott) | Text-based PDF, Planet eBook, 194 pages  | Typical PDF problems: running footer with the title on every page, page numbers, words hyphenated across lines, a long introduction before 26 chapters. |
| 3      | Meditations, Marcus Aurelius                                   | EPUB, Project Gutenberg (ebook no. 2680) | Hard case: divided into twelve “books” of numbered paragraphs instead of ordinary chapters, with a long introduction and notes.                         |

## 10.2 Test Cases

| **Test ID** | **Requirement** | **Input**                                                         | **Expected Output**                                                     | **Status** |
|-------------|-----------------|-------------------------------------------------------------------|-------------------------------------------------------------------------|------------|
| TC-001      | FR-001          | Valid email and password on login                                 | Session cookie set, redirect to library                                 | Pending    |
| TC-002      | FR-001          | Wrong password                                                    | 401, generic error message                                              | Pending    |
| TC-003      | FR-002          | Valid EPUB file                                                   | 202, book created, job queued                                           | Pending    |
| TC-004      | FR-002          | .docx file, or .pdf extension with wrong signature                | 415 UNSUPPORTED_FILE_TYPE                                               | Pending    |
| TC-005      | FR-002, FR-014  | Same file uploaded twice by one user                              | Existing book returned, no new job                                      | Pending    |
| TC-006      | FR-003, FR-005  | Test book 1 (EPUB)                                                | Expected number of chapters and titles; licence boilerplate removed     | Pending    |
| TC-007      | FR-004          | PDF with running headers, page numbers and hyphenated line breaks | Headers and page numbers removed; hyphenated words joined               | Pending    |
| TC-008      | FR-004          | Scanned PDF without text layer                                    | Job fails with NO_TEXT_LAYER and a clear message                        | Pending    |
| TC-009      | FR-005          | Book without chapter markers                                      | Split into parts; chapters_detected = false                             | Pending    |
| TC-010      | FR-006          | Chapter with paragraphs of mixed length                           | No chunk crosses a chapter or cuts a paragraph; sizes within limits     | Pending    |
| TC-011      | FR-008          | Request 5-minute summary in Montenegrin for an English book       | Summary in Montenegrin, length within ±15 % of target                   | Pending    |
| TC-012      | FR-008, FR-014  | Same summary requested twice                                      | Second response from cache; no new usage records                        | Pending    |
| TC-013      | FR-009          | Generated summary                                                 | Every key idea references an existing chapter                           | Pending    |
| TC-014      | FR-011          | Question answered by the book                                     | Answer with at least one citation; every quote found in its chunk       | Pending    |
| TC-015      | FR-011          | Question the book cannot answer                                   | answerable = false; no invented answer; no citations                    | Pending    |
| TC-016      | FR-011          | Model returns a quote that is not in the chunk (fake)             | Citation rejected; unsupported statement removed                        | Pending    |
| TC-017      | FR-012          | Claude API fails three times during processing (fake)             | Job and book marked failed; retry continues from the last finished step | Pending    |
| TC-018      | FR-013          | Known token counts and price table                                | Correct cost per call and per book                                      | Pending    |
| TC-019      | FR-015          | User B requests a book of user A                                  | 404 BOOK_NOT_FOUND                                                      | Pending    |
| TC-020      | FR-015          | User tries to delete a demo book                                  | Request rejected; book unchanged                                        | Pending    |

## 10.3 Acceptance Criteria

The project is complete and ready for demonstration when all of the following are true:

- In a live demo on the deployed application, a user uploads a public-domain book, receives a 5-minute summary in Montenegrin and in English, asks three questions and gets answers with clickable citations that open the cited passage.

- The team presents its measured faithfulness rate for 2–3 public-domain books and the cost per book.

- All Must Have requirements (FR-001 to FR-016) are implemented.

- Failure cases are handled: unsupported file, scanned PDF, unanswerable question and API failure each produce a clear message and no broken state.

- Automated tests for the critical logic pass in CI.

- The repository has a README (how to run locally, architecture overview, known limitations) and contains no secrets.

- Both team members can explain any part of the code and the design decisions.

# 11. Project Plan

## 11.1 Milestones

| **Milestone**                          | **Deliverables**                                                                                                                                                           | **Target Date** |
|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------|
| M1: Specification and parsing (Week 1) | This document approved by the mentor; repository and project skeleton; EPUB and PDF parsing with chapter detection on 3 test books; data model; job queue.                 | 20.11.2026      |
| M2: Summarisation pipeline (Week 2)    | Main flow works end to end (happy path): chunking, chapter notes, book summary with length and language options, caching, cost tracking; first deployment of the skeleton. | 27.11.2026      |
| M3: Q&A with citations (Week 3)        | Embeddings and retrieval; grounded answers with verified citations; summary UI with links from key ideas to chapters; failure paths; automated tests for critical logic.   | 04.12.2026      |
| M4: Evaluation and demo (Week 4)       | Faithfulness evaluation and short report; UX polish; deployed application; README; 10-minute presentation and live demo.                                                   | 11.12.2026      |

## 11.2 Team Responsibilities

Both members are full-stack developers. Work is split into two tracks that touch different parts of the code base, so the members rarely edit the same files. Each member reviews the other’s pull requests, which also ensures that both can explain all of the code.

| **Member**     | **Primary Role**                   | **Key Responsibilities**                                                                                                                                                                                                                                                                                                                                             |
|----------------|------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Dino Đerekerac | Track A: document and LLM pipeline | Week 1: EPUB and PDF parsing, chapter detection, parser tests. Week 2: chunking, chapter notes, prompts. Week 3: embeddings, retrieval, grounded Q&A, citation verification. Week 4: faithfulness evaluation and report. Specification sections 1, 2, 3, 8, 10.                                                                                                      |
| Daris Velović  | Track B: platform, API and UI      | Week 1: repository skeleton, Docker Compose, data model and migrations, job queue, upload and status. Week 2: LLM client with usage recording, caching, summary endpoints and UI, first deployment. Week 3: Q&A and reader UI with clickable citations, failure paths, end-to-end test. Week 4: deployment, README, UX polish. Specification sections 4, 5, 6, 7, 9. |
| Both           | Shared                             | Parser contract, weekly integration, code review, weekly progress report, sections 11–12, final presentation and demo.                                                                                                                                                                                                                                               |

## 11.3 Risk Assessment

| **Risk**                                           | **Probability** | **Impact** | **Mitigation**                                                                                                                                                          |
|----------------------------------------------------|-----------------|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Summaries contain claims the book does not support | High            | High       | Prompts restricted to the provided text; summaries built only from chapter notes; faithfulness measured in Week 4 and reported honestly; unsupported examples analysed. |
| Messy PDFs break parsing or chapter detection      | High            | Medium     | Three varied test books from Week 1; fallback to fixed-size parts; EPUB as the primary demo format; limitations documented.                                             |
| LLM cost higher than expected                      | Medium          | Medium     | Caching from the start; cheaper model for the map step; cost recorded per call; cost cap per book; fakes in automated tests.                                            |
| Scope creep (stretch goals started too early)      | High            | Medium     | Stretch goals only after the Definition of Done is met; weekly check against this document with the mentor.                                                             |
| Claude or embedding API rate limits or outage      | Medium          | High       | Retries with back-off; limited concurrency; resumable jobs; demo books processed in advance and cached.                                                                 |
| Weak or inconsistent Montenegrin output            | Medium          | Medium     | Explicit language instructions in the prompt; both members review Montenegrin summaries; examples included in the evaluation.                                           |
| Deployment problems close to the demo              | Medium          | High       | Skeleton deployed in Week 2; same Docker Compose setup locally and on the host.                                                                                         |
| Team member unavailable                            | Medium          | High       | Clear contracts between tracks; pull-request reviews keep both members familiar with all code; weekly tracker shows blockers early.                                     |
| Copyright issues with uploaded books               | Low             | High       | Uploads private to the uploader; demo library only public domain; no public launch without legal review.                                                                |

# 12. Revision History

| **Version** | **Date**   | **Author**                    | **Changes**                                    |
|-------------|------------|-------------------------------|------------------------------------------------|
| 1.0         | 20.11.2026 | Daris Velović, Dino Đerekerac | Initial version, submitted for mentor approval |
| 1.1         | 20.11.2026 | Daris Velović                 | Data model implemented: ON DELETE rules for chapter_summaries, key_ideas, citations and llm_usage.job_id; composite foreign key from chunks to chapters; decision on enumerated values (Appendix D) |
| 1.2 | 21.11.2026 | Daris Velović | Same-book rules enforced by the database: book_id and composite foreign keys on chapter_summaries, key_ideas and citations; CHECK that emails are stored in lower case; new rule that a citation references a chunk of the question’s book (6.3) |
| 1.3 | 21.11.2026 | Daris Velović | Job queue implemented: parser failures fail a job at once, other errors are retried up to 3 attempts (Appendix C); decisions on job retries and job results (Appendix D) |
| 1.4 | 21.11.2026 | Daris Velović | Authentication, upload and job status implemented: register response and EMAIL_TAKEN, cookie name and Secure rule (7.3); 503 QUEUE_UNAVAILABLE on upload (7.1, 7.2); books.file_path is relative to the storage directory (6.2); decisions on upload without a queue and on the login answer (Appendix D) |
| 1.5 | 23.11.2026 | Daris Velović, Dino Đerekerac | parse_book contract 1.1: a book without a metadata title keeps the uploaded file name as its title (6.2); decision on the book title (Appendix D). EPUB files are read with zipfile and BeautifulSoup instead of EbookLib (5.3, 9, Appendix B; decision in Appendix D) |

# Appendices

## A. Glossary

| **Term**                   | **Definition**                                                                      |
|----------------------------|-------------------------------------------------------------------------------------|
| Grounding                  | Making the model answer only from supplied source text                              |
| Hallucination              | A statement produced by the model that the source does not support                  |
| Claim                      | One statement in a summary that can be checked against the book as true or false    |
| Top-k retrieval            | Returning the k passages most similar to the question (k = 8 by default)            |
| Cosine similarity          | Measure of how close two embeddings are; used to rank passages                      |
| HNSW                       | Index type in pgvector for fast approximate nearest-neighbour search                |
| Idempotent                 | Safe to run again: repeating a step gives the same result without duplicates        |
| Happy path                 | The main scenario in which everything works as expected                             |
| Public domain              | Works no longer protected by copyright, for example books from Project Gutenberg    |
| Front matter / back matter | Parts of a book before and after the main text: title page, preface, index, licence |

## B. References

- AI-DEV 2026, Team Project Proposals, Project 4: bookcrunch.ai (project brief, evaluation criteria and Definition of Done)

- Software Specification Document template, DevelopersLab, Coinis

- Project repository: https://github.com/DinoDj123/bookcrunchai

- Project Gutenberg, source of public-domain books: https://www.gutenberg.org

- Planet eBook, source of the PDF test book: https://www.planetebook.com

- Claude API documentation: https://docs.claude.com

- Voyage AI embeddings documentation: https://docs.voyageai.com

- FastAPI documentation: https://fastapi.tiangolo.com

- Next.js documentation: https://nextjs.org/docs

- pgvector: https://github.com/pgvector/pgvector

- ARQ job queue: https://arq-docs.helpmanual.io

- PyMuPDF documentation: https://pymupdf.readthedocs.io

- Beautiful Soup documentation: https://www.crummy.com/software/BeautifulSoup/bs4/doc/

## C. Additional Diagrams

Book status and job status:

| **Object**       | **States and transitions**                                                                                    |
|------------------|---------------------------------------------------------------------------------------------------------------|
| Book             | uploaded → processing → ready. From processing the book can go to failed; retry moves it back to processing.  |
| Process-book job | queued → running (stages: parsing → chunking → embedding → chapter notes) → done, or failed: at once when the parser rejects the file, after 3 attempts on other errors. |
| Summary          | queued → running → ready, or failed. A ready summary is permanent (cache).                                    |

Sequence for a question (FR-011):

1.  The browser sends the question to POST /books/{id}/questions.

2.  The API checks ownership and that the book is ready.

3.  The API embeds the question and reads the 8 nearest chunks of the book from pgvector.

4.  The API sends the question and the numbered passages to the Claude API.

5.  The API validates the structured answer and verifies every quote against its chunk.

6.  The API stores the question, answer, citations and usage, and returns the answer.

7.  The browser shows the answer; selecting a citation opens the chapter reader at the cited chunk.

## D. Design Decision Log

Significant decisions and the alternatives that were rejected.

| **Decision**       | **Chosen**                                                                | **Alternatives rejected and why**                                                                                                                                         |
|--------------------|---------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Back-end structure | Modular monolith plus one worker                                          | Microservices: too much overhead for two developers. Pipeline inside the request: lost on timeout or restart.                                                             |
| Job queue          | ARQ with Redis                                                            | Celery: more configuration than the project needs. FastAPI background tasks: no retries, jobs lost on restart. RQ: synchronous and does not run natively on Windows.      |
| Vector storage     | pgvector in PostgreSQL                                                    | Dedicated vector store: one more service to run and keep in sync, with no benefit at this data size.                                                                      |
| Embedding model    | Hosted multilingual model (Voyage AI)                                     | English-only model: Montenegrin questions would not match English passages. Local model: no API cost, but a larger image and more memory on the host. Kept as a fallback. |
| Summarisation      | Chapter notes once per book, then one reduce step per length and language | Summarising the chapters again for every length and language: several times the cost. Whole book in one request: too large and too costly, and less reliable.             |
| Models             | Cheaper model for chapter notes, stronger model for reduce and Q&A        | Strongest model everywhere: higher cost for the step with the most tokens. To be revisited with the faithfulness results.                                                 |
| Citations          | Model returns chunk number and verbatim quote; backend verifies the quote | Trusting citations without checking: a wrong citation is worse than none. The built-in citations feature of the Claude API will be evaluated in Week 3 as an alternative. |
| Authentication     | Email and password, JWT in httpOnly cookie (to be confirmed)              | Anonymous sessions: simpler, but private books would be tied to one browser. Token in local storage: readable by scripts.                                                 |
| Caching scope      | Per user and book                                                         | Sharing results between users who upload the same file: saves cost but complicates the privacy rules.                                                                     |
| File storage       | Local disk volume                                                         | Object storage (S3): unnecessary for a single-host demo.                                                                                                                  |
| Status updates     | Polling                                                                   | WebSockets or server-sent events: more moving parts for little gain.                                                                                                      |
| Deployment         | Docker Compose on one host                                                | Split hosting (frontend on a platform, backend elsewhere): two deployments and cross-origin cookies.                                                                      |
| Enumerated values  | VARCHAR columns with CHECK constraints; allowed values in Python enums    | PostgreSQL ENUM types: adding a value needs ALTER TYPE in a migration. No database check at all: invalid values would only be caught by the application.                 |
| Same-book links | book_id plus composite foreign keys, for example (chapter_id, book_id) → chapters (id, book_id) | Check in the application only: a bug could link a citation or key idea to another user’s book and show its text (privacy, FR-015). Database trigger: harder to read and test than a foreign key. |
| Job retries | Parser failures (unsupported, damaged, protected or scanned file, parser bug, parser running over 5 minutes) fail the job at once; other errors (database, network) are retried by ARQ, 3 attempts in total. A scheduled task fails jobs left “running” by a worker that died, so a book never stays in “processing” (4.5) | Retrying every failure 3 times: the same file fails the same way, so the user would only wait longer for the same message. Own retry loop in the task: repeats what ARQ does and does not survive a worker restart. |
| Job results | Kept only in PostgreSQL; ARQ stores no result in Redis (keep_result = 0) and the ARQ job ID is the job’s UUID | Keeping ARQ results: a second copy of the status, and a kept result blocks queuing the same job ID again, which retrying a failed job needs. |
| Upload when the queue is down | The book, its job and the stored file are removed and the API answers 503 QUEUE_UNAVAILABLE; the user uploads again | Keeping the book with a job nobody will run: it would stay “uploaded” forever, and uploading the file again would only return that stuck book (FR-014). Enqueuing before the database commit: the worker could look for a job that is not saved yet. |
| Login answer | The same 401 INVALID_CREDENTIALS for an unknown email and a wrong password, and a password hash is checked in both cases | Separate messages or a faster answer for unknown emails: would reveal which addresses have an account. |
| Book title without metadata | The parser reports `title_from_metadata = False` and the worker keeps the title set at upload (the cleaned original file name) | Parser falls back to the stored file name: files are stored as `<uuid>.<ext>`, so the title would be a UUID. Passing the original file name to `parse_book()`: changes the function's signature (contract 2.0) for a value the database already has. |
| EPUB reading | Python’s zipfile module and BeautifulSoup with the lxml parser read the package file, the spine and the navigation directly | EbookLib (named in versions 1.0–1.4): no control over which error becomes which parser error code, no help with the element-level anchors that chapter splitting needs, and one more dependency under the AGPL licence for about 80 lines of package and navigation reading. |

## E. Open Questions for the Mentor

| **\#** | **Question**                                                                                                       | **Current assumption**                                                                                               |
|--------|--------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 1      | Is email and password login with JWT the expected approach, or is a simpler way of identifying users acceptable?   | Email and password with JWT, as described in 7.3                                                                     |
| 2      | Where should the application be deployed, and is a server provided?                                                | One Linux virtual machine with Docker Compose                                                                        |
| 3      | Are API keys and a budget provided for the Claude API? Is a second paid API (Voyage AI) acceptable for embeddings? | Team uses provided keys; Voyage AI for embeddings, local model as fallback                                           |
| 4      | PyMuPDF is licensed under AGPL. Is that acceptable for this project?                                               | Acceptable for an educational project; a public launch would need a licence review. Alternative: pypdf or pdfplumber |
| 5      | Is a sample of at least 25 claims per book enough for the faithfulness evaluation?                                 | 25 claims per book and language, 3 books                                                                             |
| 6      | Are the milestone dates in 11.1 correct, and when is the final demo?                                               | Weekly milestones from 20.11.2026 to 11.12.2026                                                                      |
| 7      | Is an English-only interface acceptable if summaries and answers are available in Montenegrin?                     | English interface in the MVP                                                                                         |
