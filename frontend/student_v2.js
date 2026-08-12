// The URL where our FastAPI backend is running
const API_BASE = "http://localhost:8000";

// Grab references to the HTML elements we need to interact with
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatBox = document.getElementById('chat-box');
const imageUploadBtn = document.getElementById('image-upload-btn');
const imageUploadInput = document.getElementById('image-upload');

/**
 * Helper function to construct a message bubble and add it to the chat box.
 * @param {string} sender - Who is sending the message ('user', 'ai', or 'system')
 * @param {string} text - The message content
 * @param {string} [imageUrl] - Optional image URL to display as a thumbnail
 */
function appendMessage(sender, text, imageUrl) {
    // Create the main wrapper div for the message
    const msgDiv = document.createElement('div');
    // Apply different CSS classes depending on who sent the message
    msgDiv.className = `message ${sender === 'user' ? 'user-message' : 'ai-message'}`;
    
    // Create a span for the sender's name
    const senderName = document.createElement('span');
    senderName.className = 'sender-name';
    senderName.textContent = sender === 'user' ? 'You' : 'CurriculumLens';

    let contentDiv = null;

    // If an image was provided (for user image uploads), show a thumbnail
    if (imageUrl) {
        const img = document.createElement('img');
        img.src = imageUrl;
        img.className = 'chat-image-preview';
        img.alt = 'Uploaded image';
        msgDiv.appendChild(senderName);
        msgDiv.appendChild(img);

        // If there is also text alongside the image, add it below
        if (text) {
            const caption = document.createElement('div');
            caption.className = 'document-body';
            caption.textContent = text;
            msgDiv.appendChild(caption);
            contentDiv = caption;
        }
    } else {
        // Standard text-only message
        const contentBody = document.createElement('div');
        contentBody.className = 'document-body markdown-body';
        
        // If the message is from the AI, we use the Marked library to parse Markdown into HTML
        if (sender === 'ai' && typeof marked !== 'undefined') {
            contentBody.innerHTML = marked.parse(text);
        } else {
            contentBody.textContent = text;
        }

        msgDiv.appendChild(senderName);
        msgDiv.appendChild(contentBody);
        contentDiv = contentBody;
    }

    chatBox.appendChild(msgDiv);
    
    // Automatically scroll the chat box to the very bottom so the newest message is visible
    chatBox.scrollTop = chatBox.scrollHeight;

    // Return the content element so callers can update it later (e.g., streaming)
    return contentDiv;
}



// ==========================================
// Text Chat Logic
// ==========================================

// Listen for when the user submits a text question
chatForm.addEventListener('submit', async (e) => {
    // Prevent the page from reloading
    e.preventDefault();
    
    // Get the text from the input box and remove trailing spaces
    const message = chatInput.value.trim();
    if (!message) return; // Do nothing if the message is empty

    // Step 1: Add the user's message to the chat interface immediately
    appendMessage('user', message);
    // Clear the input box so they can type another question
    chatInput.value = '';

    // Show a "thinking" indicator while waiting for the AI
    appendMessage('ai', 'Searching curriculum and generating answer...');

    try {
        // Step 2: Send the question to the backend server via HTTP POST
        const response = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // We must convert our JavaScript object into a JSON string
            body: JSON.stringify({ message: message })
        });

        // Remove the "thinking" message
        chatBox.removeChild(chatBox.lastChild);
        
        // Step 3: Handle the server's streaming response
        if (response.ok) {
            const contentType = response.headers.get("content-type");
            if (contentType && contentType.includes("application/json")) {
                const data = await response.json();
                if (data.type === "problem_list") {
                    renderProblemList(data.topic, data.problems);
                    return;
                }
            }
            // Setup an empty message bubble for the AI — appendMessage now returns the content div directly
            const targetDiv = appendMessage('ai', '');
            
            let fullText = '';
            
            // Prepare to read the stream chunk by chunk
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            
            while(true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                // Decode the chunk of data from the stream
                const chunk = decoder.decode(value, { stream: true });
                fullText += chunk;
                
                // Update the chat bubble in real-time using the direct element reference
                if (typeof marked !== 'undefined') {
                    targetDiv.innerHTML = marked.parse(fullText);
                } else {
                    targetDiv.textContent = fullText;
                }
                
                // Keep the chat scrolled to the bottom
                chatBox.scrollTop = chatBox.scrollHeight;
            }
        } else {
            // Throw an error if the server returned a bad status code
            const errorText = await response.text();
            throw new Error(errorText || 'Chat request failed');
        }
    } catch (error) {
        // Remove the "thinking" message if it exists
        const lastMsg = chatBox.lastChild;
        if (lastMsg && lastMsg.textContent.includes('Searching curriculum')) {
            chatBox.removeChild(lastMsg);
        }
        // Catch network errors or exceptions and display them in the chat
        appendMessage('system', `Error: ${error.message}`);
    }
});


// ==========================================
// Image Upload Logic
// ==========================================

// When the attach button is clicked, trigger the hidden file input
imageUploadBtn.addEventListener('click', () => {
    imageUploadInput.click();
});

// When the user selects an image file
imageUploadInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // Step 1: Create a local preview URL for the image and show it in chat
    const previewUrl = URL.createObjectURL(file);
    appendMessage('user', 'Uploaded an image for analysis', previewUrl);

    // Show a "thinking" indicator
    appendMessage('ai', 'Analyzing your image and searching curriculum...');

    // Step 2: Send the image to the backend /image-query endpoint
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE}/image-query`, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        // Remove the "thinking" message
        chatBox.removeChild(chatBox.lastChild);

        if (response.ok) {
            // Build a combined response: first show what the AI saw, then the curriculum match
            let fullResponse = '';
            if (data.description) {
                fullResponse += `**What I see in your image:**\n\n${data.description}\n\n---\n\n`;
            }
            fullResponse += `**Related curriculum content:**\n\n${data.response}`;
            
            appendMessage('ai', fullResponse);
        } else {
            throw new Error(data.detail || 'Image analysis failed');
        }
    } catch (error) {
        // Remove the "thinking" message if it exists
        const lastMsg = chatBox.lastChild;
        if (lastMsg && lastMsg.textContent.includes('Analyzing')) {
            chatBox.removeChild(lastMsg);
        }
        appendMessage('system', `Error: ${error.message}`);
    }

    // Reset the file input so the same file can be uploaded again if needed
    imageUploadInput.value = '';
});

/**
 * Renders a structured list of practice problems as a responsive card grid.
 * @param {string} topic - The topic name
 * @param {Array} problems - The list of problem objects
 */
function renderProblemList(topic, problems) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message ai-message problem-list-message';
    
    const senderName = document.createElement('span');
    senderName.className = 'sender-name';
    senderName.textContent = 'CurriculumLens';
    msgDiv.appendChild(senderName);
    
    const title = document.createElement('h3');
    title.className = 'problem-list-title';
    title.textContent = `Practice Problems on: ${topic}`;
    msgDiv.appendChild(title);
    
    if (!problems || problems.length === 0) {
        const noProblems = document.createElement('div');
        noProblems.className = 'document-body';
        noProblems.textContent = 'No practice problems found in the database for this topic.';
        msgDiv.appendChild(noProblems);
        chatBox.appendChild(msgDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
        return;
    }
    
    const gridDiv = document.createElement('div');
    gridDiv.className = 'problem-grid';
    
    problems.forEach(p => {
        const card = document.createElement('div');
        card.className = 'problem-card';
        
        // Course Code badge
        const badge = document.createElement('span');
        badge.className = 'course-badge';
        badge.textContent = p.course_code || 'General';
        card.appendChild(badge);
        
        // Question number
        if (p.question_number && p.question_number !== 'Unknown') {
            const qNum = document.createElement('span');
            qNum.className = 'question-number-tag';
            qNum.textContent = `Q${p.question_number}`;
            card.appendChild(qNum);
        }
        
        // Question text
        const qText = document.createElement('p');
        qText.className = 'problem-text';
        qText.textContent = p.text;
        card.appendChild(qText);
        
        // Image thumbnail if available
        if (p.image_url && p.image_url !== 'None' && p.image_url !== '') {
            const imgContainer = document.createElement('div');
            imgContainer.className = 'thumbnail-container';
            
            const img = document.createElement('img');
            img.src = p.image_url;
            img.className = 'problem-thumbnail';
            img.alt = `Circuit diagram or figure for question`;
            
            // When thumbnail is clicked, open full-screen modal
            imgContainer.addEventListener('click', () => {
                openImageModal(p.image_url);
            });
            
            imgContainer.appendChild(img);
            card.appendChild(imgContainer);
        }
        
        gridDiv.appendChild(card);
    });
    
    msgDiv.appendChild(gridDiv);
    chatBox.appendChild(msgDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
}

/**
 * Opens a full-screen zoom modal for circuit diagrams.
 * @param {string} imageUrl - Cloudinary URL of the image
 */
function openImageModal(imageUrl) {
    let modal = document.getElementById('global-image-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'global-image-modal';
        modal.className = 'image-modal';
        
        const modalImg = document.createElement('img');
        modalImg.id = 'global-modal-img';
        modalImg.className = 'modal-content';
        
        const closeBtn = document.createElement('span');
        closeBtn.className = 'modal-close';
        closeBtn.innerHTML = '&times;';
        closeBtn.addEventListener('click', () => {
            modal.style.display = 'none';
        });
        
        // Close modal when clicking outside the image
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
        
        modal.appendChild(closeBtn);
        modal.appendChild(modalImg);
        document.body.appendChild(modal);
    }
    
    const modalImg = document.getElementById('global-modal-img');
    modalImg.src = imageUrl;
    modal.style.display = 'flex';
}
