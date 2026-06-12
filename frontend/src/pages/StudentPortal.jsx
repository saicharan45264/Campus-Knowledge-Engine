import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const StudentPortal = () => {
    const [query, setQuery] = useState('');
    const [messages, setMessages] = useState([]);
    const { token } = useAuth();

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!query.trim()) return;

        const newMsg = { text: query, sender: 'user' };
        setMessages(prev => [...prev, newMsg]);
        setQuery('');

        try {
            const response = await Api.askQuery(query, token);
            setMessages(prev => [...prev, { text: response.answer || response.response, sender: 'bot' }]);
        } catch (error) {
            setMessages(prev => [...prev, { text: 'Error: ' + error.message, sender: 'bot' }]);
        }
    };

    return (
        <div className="section active">
            <div className="portal-container" style={{ maxWidth: '1200px' }}>
                <h2>Ask Campus Knowledge Engine</h2>
                <div className="chat-container" style={{ height: '60vh', minHeight: '500px' }}>
                    {messages.map((m, i) => (
                        <div key={i} className={`chat-message ${m.sender}`}>
                            <ReactMarkdown>{m.text}</ReactMarkdown>
                        </div>
                    ))}
                </div>
                <form onSubmit={handleSubmit} className="query-form">
                    <input 
                        type="text" 
                        placeholder="Ask a question about your curriculum, policies, etc..." 
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        required 
                    />
                    <button type="submit" className="btn-primary">Ask</button>
                </form>
            </div>
        </div>
    );
};

export default StudentPortal;
