# AI News Intelligence Agent for Ribbon Communications

## Overview

This project is the first version of an AI news intelligence agent designed to monitor daily news and identify developments that may affect Ribbon Communications.

The agent collects recent articles from trusted sources, filters them against Ribbon’s business profile, enriches them with verified context, evaluates likely business impact, and produces a daily intelligence brief. Its goal is to turn large volumes of market news into a focused, decision-useful summary.

---

## Objective

The purpose of this project is to help track external developments that matter to Ribbon Communications across its core business areas, customer base, regions, and distribution model.

The system is built to answer a simple question every day:

**Which news from the last 24 hours is most relevant for Ribbon, and why does it matter?**

---

## Company Scope

This version is configured for **Ribbon Communications** with the following business profile:

- **Sector:** Telecoms
- **Segment 1:** Cloud & Edge  
  - Customers: operators, enterprises, government, defense
- **Segment 2:** IP Optical  
  - Customers: mobile operators, data centers, cloud, government
- **Geographies:** US, EMEA, Asia Pacific
- **Distribution model:** hybrid, primarily direct, with indirect channels through system integrators, distributors/resellers, and operator partners depending on project and region

---

## What the Agent Does

The agent performs the following workflow:

1. **Collects news** from selected trusted business, financial, telecom, and official sources
2. **Retrieves article metadata** for recent publications
3. **Extracts accessible article content** from source URLs when possible
4. **Filters and ranks articles** based on relevance to Ribbon’s business profile
5. **Enriches selected articles** with additional verified context
6. **Assesses likely business impact** on Ribbon
7. **Stores processed results** locally
8. **Generates a daily intelligence brief** with the most relevant items

---

## Core Features

### News Discovery
The agent searches for recent articles using a curated set of reliable sources and targeted search logic.

### Article Relevance Scoring
Each article is evaluated according to its relevance to Ribbon’s:
- products and segments
- customer groups
- geographies
- channel and distribution ecosystem
- market drivers, regulation, procurement, partnerships, and competition

### Context Enrichment
Relevant articles are supplemented with additional information from recognized and verified sources such as:
- official company announcements
- investor relations pages
- regulatory publications
- operator and partner communications
- established media outlets

### Business Impact Assessment
The agent does not stop at general sentiment. It estimates the likely business effect on Ribbon using:
- **impact label:** positive, negative, neutral, or mixed
- **impact score:** simple numeric scale
- **confidence level:** low, medium, high

### Daily Digest Generation
The final output is a concise intelligence brief designed to highlight what matters most and explain why.

---

## Output

The project generates a daily report focused on the previous day’s news.

Each selected item includes:
- headline
- source
- publication date
- short enriched summary
- why it matters for Ribbon
- affected segment(s)
- affected customer group(s)
- affected geography
- impact label and score
- supporting sources

The report is designed to be readable by someone who wants a fast but structured market update.

---

## Project Structure

```bash
.
├── notebook.ipynb
├── .env
├── data/
│   ├── raw/
│   └── processed/
└── outputs/
