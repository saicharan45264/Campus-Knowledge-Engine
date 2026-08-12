# M.A.C.H UI

Welcome to the **M.A.C.H** project! This repository houses the user interface for the M.A.C.H platform, providing intuitive and seamless portals for both administrators and students.

## 🌟 Overview

The frontend is designed with a modern, glassmorphic aesthetic to deliver a premium and engaging user experience. Built natively, it remains extremely fast, lightweight, and highly accessible.

### Portals
- **Student Dashboard**: A dynamic, interactive space where students can leverage search and retrieve formatted knowledge dynamically rendered on the client side.
- **Admin Dashboard**: A robust management interface for administrators to oversee the system, upload resources, and manage components efficiently.

## 🎨 Architecture & Stack

This project emphasizes modularity and performance:

*   **HTML5 & CSS3**: Semantic structure with a custom-built design system (dark modes, glassmorphic overlays, fluid typography using Google Fonts).
*   **JavaScript (ES6+)**: Modular client-side architecture (`src/js/core` and `src/js/pages`) handling dynamic rendering and API interactions.
*   **Modularity**: CSS is broken down logically into variables, layouts, and components for ultimate maintainability.

## 📂 Directory Structure

```text
.
├── public/                 # Static assets and entry points
│   ├── login.html          # Unified authentication page
│   ├── admin.html          # Admin portal interface
│   └── student.html        # Student portal interface
├── src/                    # Source code (JS & CSS modules)
│   ├── css/                # Modular CSS design system
│   │   ├── variables.css   # Theme tokens & colors
│   │   ├── base.css        # Resets & base element styling
│   │   ├── layout.css      # Grid & flexbox layouts
│   │   ├── components.css  # Reusable UI components
│   │   └── main.css        # Main stylesheet aggregator
│   └── js/                 # Modular JavaScript logic
│       ├── core/           # Shared utilities and API handlers
│       │   ├── api.js      # API fetch abstractions
│       │   ├── auth.js     # Authentication state management
│       │   └── utils.js    # Helper functions
│       └── pages/          # Page-specific business logic
│           ├── login.js
│           ├── admin.js
│           └── student.js
├── package.json            # Local dev environment configuration
└── package-lock.json       # Dependency lockfile
```

## 🚀 Getting Started Locally

You can spin up the frontend instantly using any local server. The repository comes pre-configured with a lightweight `serve` script.

### Prerequisites
Make sure you have [Node.js](https://nodejs.org/) installed on your machine.

### Installation & Run

1. **Install dependencies:**
   ```bash
   npm install
   ```

2. **Start the development server (serves the public directory):**
   ```bash
   npm run dev
   ```

3. **View the Application:**
   Open your browser and navigate to `http://localhost:3000/login.html`

---
*Designed with precision to make navigation seamless and beautiful.*
