FieldSurveyGO – Smart Offline Survey & Data Management Platform

A professional offline field survey and data management application built using Python, PyQt/PySide6, and SQLite.
FieldSurveyGO helps researchers, organizations, and field teams efficiently collect, manage, analyze, and export survey data without requiring an internet connection.

🚀 Features
📁 Project & User Management
📝 Dynamic Form Creation
📊 Offline Survey Data Collection
✅ Validation-Based Data Entry
🔍 Real-Time Search & Filtering
✏️ Record Editing & Deletion
📤 CSV & JSON Export
📥 JSON Import with Duplicate Protection
📈 Basic Statistical Analysis & ASCII Charts
🎨 Light/Dark Theme Support
🖥️ Responsive Professional GUI
💾 SQLite Database Integration
🔄 Automated Data Refresh
🌍 Cross-Platform Support (Windows/Linux)
🛠️ Tech Stack
Python
PyQt / PySide6
SQLite
JSON & CSV Handling
📸 Application Modules
1️⃣ Project & User Management
Create and manage survey projects
Add enumerators/admin users
Select active project and user
2️⃣ Form Designer
Create custom survey forms
Add multiple field types:
Text
Number
Select Dropdown
Date
File Path
Reorder and manage fields dynamically
3️⃣ Data Collection
Guided offline data entry interface
Metadata support:
Device ID
Latitude
Longitude
Validation-based response submission
4️⃣ Import & Export
Export survey responses to:
CSV
JSON
Import JSON responses safely using UUID duplicate checking
5️⃣ Statistics & Analysis
Value count analysis
Simple ASCII-based chart visualization
Quick field statistics
📂 Project Structure
FieldSurveyGO/
│
├── PyQtAp.py          # Main application source code
├── survey1.db         # SQLite database
├── README.md
└── exports/           # Exported CSV/JSON files
⚙️ Installation
1️⃣ Clone the Repository
git clone https://github.com/your-username/FieldSurveyGO.git
cd FieldSurveyGO
2️⃣ Install Dependencies
pip install PySide6
▶️ Run the Application
Windows
python PyQtAp.py
Linux / macOS
python3 PyQtAp.py
🗄️ Database

The application uses SQLite for secure offline storage.

Main database tables include:

Projects
Users
Forms
Fields
Responses
Answers
Imports
📤 Export Formats
CSV Export

Used for spreadsheet analysis and reporting.

JSON Export

Used for backup, synchronization, and importing data.

📥 Import System

The JSON import system includes:

UUID-based duplicate protection
Safe merging of survey responses
Import history tracking
🎨 UI Features
Modern professional interface
Fusion-based custom theme
Light & Dark mode support
Responsive layouts
Styled tables and forms
📊 Example Use Cases
Field Research Surveys
NGO Data Collection
Household Surveys
Academic Research
Rural Development Studies
Offline Census Collection
🔒 Advantages
Fully Offline Functionality
Lightweight & Fast
Easy to Use
Modular Architecture
Secure Local Database
Cross-Platform Compatibility
