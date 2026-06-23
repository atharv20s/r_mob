# r_mob

This is a FastAPI backend project structured to handle AI operations, AWS services, and core backend endpoints.

## Features

- **FastAPI Backend**: Sleek, modern Python web framework for building APIs.
- **AWS Services Integration**: Boto3 client configuration for cloud services (e.g., S3, SNS, SES).
- **AI Integrations**: Ready-to-go integrations for AI providers (Google Gemini and OpenAI).
- **Modular Project Structure**: Separated api routers, business services, and settings loader.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git

### Installation

1. Clone the repository (if already pushed to remote):
   ```bash
   git clone https://github.com/atharv20s/r_mob.git
   cd r_mob
   ```

2. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables by copying `.env.example` to `.env` and updating the values:
   ```bash
   copy .env.example .env
   ```

5. Run the development server:
   ```bash
   uvicorn src.main:app --reload
   ```

6. Open your browser and navigate to `http://127.0.0.1:8000/docs` to view the interactive Swagger documentation.
