// The URL where our FastAPI backend is running
const API_BASE = "http://localhost:8000";

// ==========================================
// Tab Navigation Logic
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabPanels = document.querySelectorAll('.tab-panel');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Remove active class from all buttons and panels
            tabBtns.forEach(b => b.classList.remove('active'));
            tabPanels.forEach(p => p.classList.remove('active'));

            // Add active class to clicked button and corresponding panel
            btn.classList.add('active');
            const targetId = btn.getAttribute('data-target');
            document.getElementById(targetId).classList.add('active');

            // If the user clicked the "Manage Documents" tab, refresh the table
            if (targetId === 'manage-panel') {
                fetchDocuments();
            }
        });
    });
});

// ==========================================
// Form Toggle Logic
// ==========================================
const docTypeSelect = document.getElementById('doc-type');
const syllabusFields = document.getElementById('syllabus-fields');
const pyqFields = document.getElementById('pyq-fields');
const fileInput = document.getElementById('document-upload');
const fileHint = document.getElementById('file-selection-hint');

docTypeSelect.addEventListener('change', (e) => {
    const type = e.target.value;
    if (type === 'syllabus') {
        syllabusFields.classList.remove('hidden');
        pyqFields.classList.add('hidden');
        fileInput.removeAttribute('multiple');
        fileHint.textContent = "Please select a single PDF for the syllabus.";
    } else if (type === 'pyq') {
        pyqFields.classList.remove('hidden');
        syllabusFields.classList.add('hidden');
        fileInput.setAttribute('multiple', 'true');
        fileHint.textContent = "You can select multiple PYQ PDFs at once.";
    }
});

// Update the hint when files are selected
fileInput.addEventListener('change', (e) => {
    const count = e.target.files.length;
    if (count > 0) {
        fileHint.textContent = `${count} file(s) selected.`;
    }
});

// ==========================================
// Upload Logic
// ==========================================
const uploadForm = document.getElementById('upload-form');
const uploadStatusDiv = document.getElementById('upload-status');

uploadForm.addEventListener('submit', async (e) => {
    // Prevent the page from reloading when the form is submitted
    e.preventDefault();
    
    const docType = docTypeSelect.value;
    const files = fileInput.files;

    if (!docType || files.length === 0) return;

    // We use FormData to send a file via HTTP POST
    const formData = new FormData();
    formData.append('doc_type', docType);

    if (docType === 'syllabus') {
        const dept = document.getElementById('department').value;
        const year = document.getElementById('syllabus-year').value;
        if (!dept || !year) {
            alert("Please select Department and Year for Syllabus.");
            return;
        }
        formData.append('department', dept);
        formData.append('year', year);
    } else if (docType === 'pyq') {
        const courseCode = document.getElementById('course-code').value;
        if (!courseCode) {
            alert("Please enter the Course Code for PYQs.");
            return;
        }
        formData.append('course_code', courseCode);
    }

    // Append all selected files
    for (let i = 0; i < files.length; i++) {
        formData.append('files', files[i]);
    }

    // Show a loading message
    uploadStatusDiv.classList.remove('hidden');
    uploadStatusDiv.className = 'status-message info';
    uploadStatusDiv.textContent = 'Uploading and processing... This may take a moment.';
    
    try {
        // Send the POST request to the backend /upload endpoint
        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        
        // If the backend returns a successful response (HTTP 200 OK)
        if (response.ok) {
            uploadStatusDiv.className = 'status-message success';
            uploadStatusDiv.textContent = `Success: ${data.message}`;
            // Refresh the document table to show the newly uploaded file
            fetchDocuments();
            
            // Reset the form (except for doc type selection)
            fileInput.value = '';
            document.getElementById('course-code').value = '';
            fileHint.textContent = '';
        } else {
            // If the backend returns an error code
            throw new Error(data.detail || 'Upload failed');
        }
    } catch (error) {
        // Catch any network errors or thrown errors
        uploadStatusDiv.className = 'status-message error';
        uploadStatusDiv.textContent = `Error: ${error.message}`;
    }
});

// ==========================================
// Fetch and Display Documents Logic
// ==========================================
async function fetchDocuments() {
    const tableBody = document.getElementById('documents-table-body');
    try {
        // Fetch the list of documents from the backend
        const response = await fetch(`${API_BASE}/documents`);
        const docs = await response.json();
        
        // Clear out any existing rows
        tableBody.innerHTML = ''; 
        
        // If the database is empty, show a message
        if (docs.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="4" style="text-align:center;">No documents uploaded yet.</td></tr>';
            return;
        }

        // Loop through each document and create a table row for it
        docs.forEach(doc => {
            const row = document.createElement('tr');
            
            // Format the timestamp into a readable local date string
            const date = new Date(doc.created_at).toLocaleString();
            
            // Construct the HTML for the row, including a Delete button
            row.innerHTML = `
                <td>${doc.filename}</td>
                <td>${doc.course_code || 'N/A'}</td>
                <td>${date}</td>
                <td>
                    <button class="delete-btn" onclick="deleteDocument('${doc.id}')">Delete</button>
                </td>
            `;
            tableBody.appendChild(row);
        });
    } catch (error) {
        console.error("Failed to fetch documents", error);
        tableBody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:red;">Failed to load documents.</td></tr>';
    }
}

// ==========================================
// Delete Document Logic
// ==========================================
// This function is triggered by the onclick attribute on the Delete buttons in the table
async function deleteDocument(id) {
    // Ask the user to confirm before proceeding
    if (!confirm('Are you sure you want to delete this document and its data?')) return;
    
    try {
        // Send a DELETE request to the backend
        const response = await fetch(`${API_BASE}/documents/${id}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            // Refresh the table to remove the deleted item
            fetchDocuments(); 
        } else {
            alert('Failed to delete document.');
        }
    } catch (error) {
        console.error("Delete error", error);
        alert('Error deleting document.');
    }
}

// Run fetchDocuments() automatically as soon as the page loads
window.addEventListener('DOMContentLoaded', fetchDocuments);

// ==========================================
// Danger Zone: Reset System Logic
// ==========================================
document.getElementById('reset-btn').addEventListener('click', async () => {
    // Require two distinct confirmations because this action is destructive
    const firstConfirm = confirm(
        'WARNING: This will permanently delete ALL documents, ALL knowledge graph data, and ALL uploaded files.\n\nThis cannot be undone. Are you absolutely sure?'
    );
    if (!firstConfirm) return;

    const secondConfirm = confirm('Last chance — confirm you want to wipe the entire system.');
    if (!secondConfirm) return;

    const resetStatus = document.getElementById('reset-status');
    resetStatus.className = 'status-message info';
    resetStatus.textContent = 'Resetting system... please wait.';

    try {
        // Send a POST request to the reset endpoint
        const response = await fetch(`${API_BASE}/reset`, { method: 'POST' });
        const data = await response.json();

        if (response.ok) {
            resetStatus.className = 'status-message success';
            resetStatus.textContent = 'System reset complete. All data has been wiped.';
            // Refresh the table (it should now be empty)
            fetchDocuments();
        } else {
            throw new Error(data.detail || 'Reset failed.');
        }
    } catch (error) {
        resetStatus.className = 'status-message error';
        resetStatus.textContent = `Error during reset: ${error.message}`;
    }
});
