# 💰 Finance Manager

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-2.3.3-green)](https://flask.palletsprojects.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-6.0-brightgreen)](https://www.mongodb.com/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg)](https://www.docker.com/)
![visitor badge](https://visitor-badge.laobi.icu/badge?page_id=https://github.com/WhoIsJayD/Finance-Manage)

A comprehensive personal finance management web application built with Flask and MongoDB. Track your income and expenses, analyze your spending habits with detailed reports, set budgets, and take full control of your financial life.

[**Watch the Demo**](#-live-demo--screenshots) · [**Report a Bug**](https://github.com/WhoIsJayD/Finance-Manager/issues) · [**Request a Feature**](https://github.com/WhoIsJayD/Finance-Manager/issues)


### 🌟 Key Features

-   📊 **Interactive Dashboard**: Get an instant overview of your finances, including income, expenses, current balance, and recent transactions.
-   💸 **Transaction Management**: Easily add, edit, and delete income and expense records with details like tags and payment methods.
-   🎯 **Budgeting System**: Set monthly budgets for different expense categories and visually track your progress against them.
-   📈 **Advanced Reporting**: Visualize your financial data with dynamic charts for monthly trends, category breakdowns, payment methods, and cash flow projections.
-   🎨 **Customizable Interface**: Personalize your experience with theme support (light/dark mode), multiple currencies, and custom date formats.
-   📧 **Email Notifications**: Receive security alerts, budget warnings, and financial summaries directly to your inbox using Resend.
-   🔐 **Secure Admin System**: A protected area for user management (grant/revoke admin rights) and viewing system logs.
-   🐳 **Dockerized**: Get up and running in minutes with the provided Docker Compose setup for a smooth deployment.


### 🎥 Live Demo & Screenshots

Click the thumbnail below to watch a video overview of the dashboard and its features.
<div align="center">
  <a href="https://res.cloudinary.com/dx9ctc074/video/upload/v1750612468/xjnwfyagyptuyalparfk.mp4">
    <img src="https://res.cloudinary.com/dx9ctc074/image/upload/v1750612741/nope92vbrk6pnq7rfylp.gif" alt="Application Demo" width="100%">
  </a>
</div>

#### 📱 Screenshots

| Transaction Page | Budget Page | Report Page | Admin Dashboard |
|:----------------:|:-----------:|:-----------:|:---------------:|
| ![Transaction Page](https://i.imgur.com/PNjjy0O.png) | ![Budget Page](https://i.imgur.com/uw27TSC.png) | ![Report Page](https://i.imgur.com/UGsW3AV.png) | ![Admin Dashboard](https://i.imgur.com/H33MA9W.png) |


### 🛠️ Tech Stack

Built with a modern and robust technology stack:

-   **Backend**: Python, Flask
-   **Database**: MongoDB (with PyMongo)
-   **Frontend**: HTML, CSS, JavaScript, Bootstrap 5
-   **Charting**: Chart.js
-   **WSGI Servers**: Gunicorn (for Linux/macOS), Waitress (for Windows)
-   **Deployment**: Docker


### 🚀 Getting Started

The fastest way to get the Finance Manager running is with Docker.

#### Prerequisites

-   [Docker](https://www.docker.com/products/docker-desktop/) and [Docker Compose](https://docs.docker.com/compose/install/)
-   Git

#### Quick Start with Docker

1.  **Clone the repository:**
    ```sh
    git clone [https://github.com/WhoIsJayD/Finance-Manager.git](https://github.com/WhoIsJayD/Finance-Manager.git)
    cd Finance-Manager
    ```

2.  **Create your environment file:**
    Copy the example file. On Linux/macOS, use `cp .env.example .env`. On Windows, use `copy .env.example .env`.

3.  **Configure your `.env` file:**
    Open the `.env` file and set the following variables. A strong `SECRET_KEY` is crucial.
    ```env
    FLASK_ENV=production
    SECRET_KEY=generate_a_very_secure_random_string_here
    RESEND_API_KEY=your_resend_api_key_for_emails
    ADMIN_SETUP_CODE=a_secure_random_code_for_first_admin_setup
    ADMIN_PASSWORD=a_strong_password_for_sensitive_admin_actions
    ```

4.  **Build and run the containers:**
    ```sh
    docker-compose up -d --build
    ```

5.  **Access the application:**
    Open your browser and navigate to `http://localhost:5000`.

6.  **Initialize the first Admin User:**
    -   Navigate to `http://localhost:5000/initialize-admin`.
    -   Enter the `ADMIN_SETUP_CODE` from your `.env` file.
    -   Fill in the form to create your admin account.


### ⚙️ Manual Installation (Without Docker)

If you prefer a manual setup:

1.  **Clone the repository and create a virtual environment:**
    ```sh
    git clone [https://github.com/WhoIsJayD/Finance-Manager.git](https://github.com/WhoIsJayD/Finance-Manager.git)
    cd Finance-Manager
    python3 -m venv venv
    source venv/bin/activate  # On Linux/macOS
    # venv\Scripts\activate   # On Windows
    ```

2.  **Install dependencies:**
    ```sh
    pip install -r requirements.txt
    ```
   

3.  **Set up MongoDB:**
    Ensure you have a running MongoDB instance and update the `MONGO_URI` in your `.env` file accordingly.

4.  **Configure and run the application:**
    -   Create and fill out the `.env` file as described in the Docker setup.
    -   **On Linux/macOS (with Gunicorn):**
        ```sh
        gunicorn --bind 0.0.0.0:5000 "app:app"
        ```
       
    -   **On Windows (with Waitress):**
        ```sh
        python run_server.py
        ```
       


### 🔒 Security Considerations

The application is built with security in mind:
-   Password hashing with **bcrypt**.
-   Rate limiting on login/registration routes to prevent brute-force attacks.
-   Secure, `HttpOnly` session cookies.
-   Admin actions like toggling user privileges require password confirmation.
-   Input validation and parameterized database queries to prevent injection attacks.

For production, always run behind a reverse proxy (like Nginx or Caddy) and enable HTTPS.



### 🤝 Contributing

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

1.  **Fork the Project**.
2.  **Create your Feature Branch** (`git checkout -b feature/AmazingFeature`).
3.  **Commit your Changes** (`git commit -m 'Add some AmazingFeature'`).
4.  **Push to the Branch** (`git push origin feature/AmazingFeature`).
5.  **Open a Pull Request**.

Please ensure your code follows the existing style and that you add comments for complex logic.



### 📄 License

Distributed under the MIT License. See `LICENSE` file for more information.


### 🙏 Acknowledgements

-   [Bootstrap](https://getbootstrap.com/)
-   [Chart.js](https://www.chartjs.org/)
-   [Flask](https://flask.palletsprojects.com/)
-   [MongoDB](https://www.mongodb.com/)
