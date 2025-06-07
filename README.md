Website Tracking
A flexible, automated system for monitoring and capturing snapshots of webpages. Built with modern Python tools to reliably track dynamic content and store historical records for analysis.

Overview
This application uses browser automation to:

Navigate and interact with multiple websites

Capture full-page screenshots and raw HTML

Handle cookies and sessions for authenticated flows

Store timestamped records in a relational database

Expose a simple API to add, view, or remove tracked URLs

Tech Stack
Language: Python 3.8+

Automation: Playwright

Web Framework: Django 

Database: PostgreSQL 

Caching/Message Broker: Redis 

Features
Configurable Tracking: Define custom intervals per site

Snapshot Storage: Screenshots + HTML dumps with metadata

Change Detection: Compare snapshots to detect updates

API Access: Retrieve tracking history and status

Extensible: Plug in new parsers or exporters easily

Getting Started
Create a Python virtual environment and install dependencies.

Configure your database connection in the app’s settings.

Run any data-initialization scripts to prepare tables and seed sample entries.

Launch the web/API server and, if used, start your background scheduler.

Use the provided endpoints or admin UI to register sites for monitoring.

API Endpoints (Examples)
Method	Path	Description
POST	/websitetracking/	Register a new URL to monitor
GET	/websitetracking/	List all monitored URLs
GET	/websitetracking/{id}/history/	Fetch snapshot history for an ID
DELETE	/websitetracking/{id}/	Remove a URL from monitoring
