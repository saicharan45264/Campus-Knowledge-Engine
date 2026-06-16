import React, { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const INTENT_META = {
    SYLLABUS:   { emoji: '📚', label: 'Syllabus' },
    TIMETABLE:  { emoji: '🗓️', label: 'Timetable' },
    CALENDAR:   { emoji: '📅', label: 'Calendar' },
    REGULATION: { emoji: '📋', label: 'Regulation' },
    EVALUATION: { emoji: '📊', label: 'Evaluation' },
    CO_PO:      { emoji: '🏛️', label: 'CO/PO' },
    FACULTY:    { emoji: '👩‍🏫', label: 'Faculty' },
    GENERAL:    { emoji: '💬', label: 'General' },
};

const SUGGESTIONS = [
    'What subjects are in my semester?',
    'What is my timetable today?',
    'When are the upcoming holidays?',
    'What is the attendance requirement?',
    'How are internal marks calculated?',
];

const StudentPortal = () => {
    const [query, setQuery] = useState('');
    const [messages, setMessages] = useState([]);
    const [isTyping, setIsTyping] = useState(false);
    const { token } = useAuth();
    const bottomRef = useRef(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, isTyping]);

    const sendQuery = async (q) => {
        const text = q.trim();
        if (!text) return;
        setMessages(prev => [...prev, { text, sender: 'user' }]);
        setQuery('');
        setIsTyping(true);
        try {
            const res = await Api.askQuery(text, token);
            setMessages(prev => [...prev, {
                text: res.answer || res.response || res.question,
                sender: 'bot',
                intent: res.intent || 'GENERAL',
            }]);
        } catch (err) {
            setMessages(prev => [...prev, { text: 'Error: ' + err.message, sender: 'bot', intent: 'GENERAL' }]);
        } finally {
            setIsTyping(false);
        }
    };

    const handleSubmit = (e) => { e.preventDefault(); sendQuery(query); };

    return (
        <div className="chat-layout">
            {/* Header */}
            <div className="chat-header">
                <div className="chat-header-icon">🧠</div>
                <div>
                    <div className="chat-header-name">Campus Knowledge Engine</div>
                    <div className="chat-header-status">
                        <span className="chat-online-dot" />
                        Ready · Ask anything about your curriculum
                    </div>
                </div>
            </div>

            {/* Messages area */}
            <div className="chat-messages">
                {messages.length === 0 ? (
                    <div className="chat-empty">
                        <div className="chat-empty-icon">🎓</div>
                        <h3>What would you like to know?</h3>
                        <p>Ask me about your timetable, syllabus, regulations, or calendar. I'll find the answer from official university documents.</p>
                        <div className="suggestion-chips">
                            {SUGGESTIONS.map(s => (
                                <button key={s} className="suggestion-chip" onClick={() => sendQuery(s)}>
                                    {s}
                                </button>
                            ))}
                        </div>
                    </div>
                ) : (
                    <>
                        {messages.map((m, i) => (
                            <div key={i} className={`bubble-row ${m.sender}`}>
                                <div className={`bubble-avatar ${m.sender}`}>
                                    {m.sender === 'bot' ? '🧠' : 'U'}
                                </div>
                                <div>
                                    {m.sender === 'bot' && m.intent && INTENT_META[m.intent] && (
                                        <div className="intent-badge">
                                            {INTENT_META[m.intent].emoji} {INTENT_META[m.intent].label}
                                        </div>
                                    )}
                                    <div className={`bubble ${m.sender}`}>
                                        {m.sender === 'bot'
                                            ? <ReactMarkdown>{m.text}</ReactMarkdown>
                                            : m.text}
                                    </div>
                                </div>
                            </div>
                        ))}

                        {isTyping && (
                            <div className="typing-row">
                                <div className="bubble-avatar bot">🧠</div>
                                <div className="typing-bubble">
                                    <span /><span /><span />
                                </div>
                            </div>
                        )}
                    </>
                )}
                <div ref={bottomRef} />
            </div>

            {/* Input */}
            <div className="chat-input-area">
                <form onSubmit={handleSubmit} className="chat-input-form">
                    <input
                        type="text"
                        placeholder="Ask about timetable, syllabus, regulations..."
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        disabled={isTyping}
                        id="chat-input"
                        autoComplete="off"
                    />
                    <button
                        type="submit"
                        className="chat-send-btn"
                        disabled={!query.trim() || isTyping}
                        id="chat-send"
                        aria-label="Send"
                    >
                        <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                    </button>
                </form>
            </div>
        </div>
    );
};

export default StudentPortal;
