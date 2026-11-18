# Production-Grade Prompt for Claude

> **Role**
>
> You are a Principal AI Architect, Principal Software Engineer, AI Research Engineer, EdTech Product Architect, Prompt Engineer, Cloud Architect, Security Architect, and DevOps Expert with 20+ years of experience.

## Instructions

Before answering, perform deep internal reasoning across architecture, AI, scalability, security, UX, cost, and implementation. **Do not reveal your reasoning.** Only provide the final conclusions.

Design a **production-ready SaaS AI Question Paper Generation Platform**.

## Vision

Build a platform where teachers generate high-quality question papers using multiple configurable sources:

- Textbook Exercise Questions
- In-text Questions
- AI Generated Questions
- Previous Year Questions
- School Question Bank

The teacher should configure source percentages or exact counts.

## Core Modules

- Authentication (RBAC)
- Multi-school SaaS
- Boards (CBSE/ICSE/State/IB/IGCSE)
- Classes
- Subjects
- Chapters
- Curriculum Management
- Question Banks
- AI Generation
- Paper Templates
- PDF/DOCX Export
- Approval Workflow
- Analytics
- Subscription
- Admin Portal

## Teacher Workflow

Teacher selects:

- Board
- Class
- Subject
- Chapters
- Exam Type
- Question Pattern
- Marks (flexible)
- Duration
- Difficulty Distribution
- Language
- Instructions
- Source Distribution

Example:

- 40% Textbook
- 20% In-text
- 20% Previous Year
- 10% School Bank
- 10% AI

## Hybrid Retrieval Strategy

Priority:

1. School Question Bank
2. Textbook Exercise
3. In-text
4. Previous Year
5. AI Generation

Generate new questions only when needed.

## Textbook Processing

Design OCR + PDF ingestion pipeline that extracts:

- Chapters
- Exercises
- Exercise Questions
- In-text Questions
- Activities
- Diagrams
- Tables
- Keywords
- Learning Outcomes

Store structured content and embeddings.

## AI Pipeline

Explain:

- RAG
- Vector Database
- Metadata Filtering
- Hybrid Search
- Re-ranking
- Duplicate Detection
- Difficulty Estimation
- Bloom Mapping
- Competency Mapping
- Validation
- Answer Key
- Rubrics
- PDF Generation

## Database

Design complete schema for:

Users
Schools
Boards
Classes
Subjects
Chapters
QuestionBank
TextbookQuestions
InTextQuestions
GeneratedQuestions
QuestionEmbeddings
GeneratedPapers
PaperVersions
Templates
Subscriptions
Usage
AuditLogs
Analytics

Include relationships, indexes, partitioning and optimization.

## APIs

Design REST APIs with request/response examples.

## AI Prompt Engineering

Provide:

- System Prompt
- Developer Prompt
- Dynamic Prompt Builder
- JSON Schema
- Validation Prompt
- Repair Prompt
- Regeneration Prompt

## Architecture

Design:

- Frontend
- Backend
- AI Service
- OCR Service
- Retrieval Service
- Queue
- Redis
- PostgreSQL
- Vector DB
- Object Storage
- Monitoring
- Logging
- Kubernetes
- CI/CD

Include ASCII diagrams.

## Security

JWT
OAuth
RBAC
Encryption
Secrets
Rate Limiting
Prompt Injection Defense
Audit Logs

## Deployment

AWS reference architecture including:

- ECS/EKS
- RDS
- Redis
- S3
- CloudFront
- SQS
- CloudWatch
- IAM
- VPC
- Autoscaling

Estimate costs for small, medium and enterprise deployments.

## Scalability

Explain scaling for:

100 teachers
10k teachers
100k teachers
1M teachers

## Roadmap

Phase-wise implementation:

MVP
Beta
Production
Enterprise
AI Enhancements

Include timelines, risks and dependencies.

## Future Features

Adaptive Exams
OCR Answer Checking
Diagram Generation
Math Rendering
Learning Analytics
AI Tutor
Voice Input
Multilingual Translation

## Output Order

1 Executive Summary
2 Functional Requirements
3 Non-functional Requirements
4 User Flow
5 AI Workflow
6 High-Level Architecture
7 Detailed Architecture
8 Database Schema
9 API Design
10 AI Pipeline
11 Prompt Engineering
12 Technology Stack
13 Folder Structure
14 Security
15 Deployment
16 CI/CD
17 Monitoring
18 Scaling
19 Cost Estimation
20 Testing
21 Roadmap
22 Future Enhancements
23 Risks
24 Recommendations

Deliver a production-grade blueprint suitable for implementation by a professional engineering team.
