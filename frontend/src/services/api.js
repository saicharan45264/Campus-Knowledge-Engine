const API_BASE_URL = window.location.origin.startsWith('file://') || window.location.origin.includes('localhost:5173') ? 'http://localhost:8000' : window.location.origin;

class Api {
    static async handleResponse(response) {
        if (!response.ok) {
            let errorMsg = 'An error occurred';
            try {
                const errorData = await response.json();
                errorMsg = errorData.detail || errorMsg;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        return response.json();
    }

    static async studentLogin(email, password) {
        const response = await fetch(`${API_BASE_URL}/auth/student/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email_or_username: email, password: password })
        });
        return this.handleResponse(response);
    }

    static async studentRegister(data) {
        const response = await fetch(`${API_BASE_URL}/auth/student/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        return this.handleResponse(response);
    }

    static async adminLogin(username, password) {
        const response = await fetch(`${API_BASE_URL}/auth/admin/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email_or_username: username, password: password })
        });
        return this.handleResponse(response);
    }

    static async askQuery(queryText, token) {
        const response = await fetch(`${API_BASE_URL}/query`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ query: queryText, session_id: "default" })
        });
        return this.handleResponse(response);
    }

    static async uploadDocument(formData, token) {
        const response = await fetch(`${API_BASE_URL}/admin/documents/upload`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            },
            body: formData 
        });
        return this.handleResponse(response);
    }

    static async getAdminDocuments(token) {
        const response = await fetch(`${API_BASE_URL}/admin/documents`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        return this.handleResponse(response);
    }

    static async deleteAdminDocument(docId, token) {
        const response = await fetch(`${API_BASE_URL}/admin/documents/${docId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        return this.handleResponse(response);
    }

    static async getAdminStudents(token) {
        const response = await fetch(`${API_BASE_URL}/admin/students`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        return this.handleResponse(response);
    }
}

export default Api;
