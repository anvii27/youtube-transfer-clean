# YouTube Video Transfer - AI-assisted Streamlit App

## Project Overview:
This project is a Streamlit web application that automates transferring videos between two YouTube channels. It leverages the YouTube Data API v3 and integrates OpenAI's GPT-based AI to assist in selecting which videos to transfer via natural language instructions.

## Features:
.OAuth authentication for both the old and new YouTube channels (installed-app flow).
.Fetch and display all uploaded videos from the old channel.
.AI-assisted video selection using OpenAI GPT to suggest videos based on user instructions.
.Efficient video downloading with yt-dlp.
.Upload videos to the new YouTube channel with original metadata (title, description, tags).
.Option to delete original videos from the old channel after successful upload.
.Local JSON log (transfer_log.json) to track processed videos and avoid duplication.
.Streamlined interactive UI with progress logging and error handling.

## Tech Stack:
.Python 3
.Streamlit for UI
.Google API Client (google-api-python-client)
.OAuth 2.0 for authentication
.yt-dlp for video downloading
.OpenAI API for AI-based video selection
.python-dotenv for environment variable management
