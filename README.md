# Majima AI - Summarization Tool

## 🚀 Overview
Majima AI is a powerful lecture summarization tool that uses **Google Gemini 2.5 Flash Lite** (via OpenRouter) to generate concise, high-quality summaries from PDF documents. It supports both English and Arabic content.

## 🛠️ Configuration & Setup

### 1. Requirements
Install the required packages:
```bash
pip install -r requirements.txt
```

### 2. Secure API Keys (.env)
This project uses a secure `.env` file to store API keys.

1. **Copy the example file:**
   ```bash
   copy .env.example .env
   ```
2. **Edit `.env`** and add your actual API keys:
   - `OPENROUTER_API_KEY`: Your OpenRouter key
   - `HF_API_KEY`: Your Hugging Face key
   - `FLASK_SECRET_KEY`: A random secret string

**⚠️ IMPORTANT:** Never commit your `.env` file to GitHub! Use `.env.example` for sharing structure.

### 3. AWS DynamoDB Configuration
The application uses **AWS DynamoDB** for storage.
**Setting up Credentials (Windows):**
```powershell
set AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY
set AWS_SECRET_ACCESS_KEY=YOUR_SECRET_KEY
set AWS_DEFAULT_REGION=us-east-1
```

## 🏃 Running the Application
```bash
python app.py
```
Access the dashboard at: `http://localhost:8000`

## 🔐 Admin Access
- **Login URL:** Go to `/login` and click **"Switch to Admin Login"** at the bottom.
- **Default Credentials:**
  - Username: `admin`
  - Password: `admin`

## ☁️ GitHub Upload Instructions
Since Git is not installed, follow these steps to upload your code:

1. **Install Git:** Download from [git-scm.com](https://git-scm.com/downloads) and install it.
2. **Initialize Repository:**
   Open your terminal in the project folder and run:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   ```
3. **Push to GitHub:**
   - Create a new repository on GitHub.com
   - Run the commands shown on GitHub:
     ```bash
     git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
     git branch -M main
     git push -u origin main
     ```

## 📂 Project Structure
- `app.py`: Main Flask application.
- `ingestion_pipeline.py`: PDF parsing & chunking.
- `summarization_pipeline.py`: AI summarization logic.
- `retrieval_pipeline.py`: RAG retrieval logic.
