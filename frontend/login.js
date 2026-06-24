// Listen for the form submission
document.getElementById('login-form').addEventListener('submit', (e) => {
    // Prevent the default behavior (which would reload the page)
    e.preventDefault();
    
    // Get the user input, convert to lowercase, and remove any extra spaces
    const username = document.getElementById('username').value.toLowerCase().trim();
    const statusDiv = document.getElementById('login-status');
    
    // Simple routing based on the Role ID entered
    if (username === 'admin') {
        window.location.href = 'admin.html'; // Redirect to Admin Dashboard
    } else if (username === 'student') {
        window.location.href = 'student.html'; // Redirect to Student Portal
    } else {
        // If the input is wrong, show an error message
        statusDiv.className = 'status-message error';
        statusDiv.textContent = 'Invalid Role ID. Please use "admin" or "student".';
    }
});
