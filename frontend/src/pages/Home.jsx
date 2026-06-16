import React from 'react';
import { useNavigate } from 'react-router-dom';

const FEATURES = [
    { icon: '📚', title: 'Syllabus & Curriculum', desc: 'Instant answers about your subjects, units, textbooks, and course outcomes.' },
    { icon: '🗓️', title: 'Timetable & Schedule', desc: 'Find your class schedule, room numbers, and daily slot timings.' },
    { icon: '📋', title: 'Regulations & Policies', desc: 'Get clarity on attendance rules, grading policies, and academic procedures.' },
    { icon: '📅', title: 'Academic Calendar', desc: 'Check exam dates, holidays, and key semester events instantly.' },
];

const Home = () => {
    const navigate = useNavigate();

    return (
        <div className="hero">
            {/* Left column */}
            <div>
                <span className="hero-eyebrow">AI-Powered · Amrita Vishwa Vidyapeetham</span>
                <h1 className="hero-title">
                    Your Campus Knowledge,{' '}
                    <span className="accent">Answered Instantly</span>
                </h1>
                <p className="hero-desc">
                    Ask any question about your curriculum, timetable, regulations, or
                    academic calendar. Our AI searches through official university documents
                    and gives you precise, cited answers — no more digging through PDFs.
                </p>

                <div className="hero-actions">
                    <button
                        className="btn-primary large"
                        onClick={() => navigate('/student/login')}
                        id="hero-student-btn"
                    >
                        Student Login
                    </button>
                    <button
                        className="btn-secondary"
                        onClick={() => navigate('/admin/login')}
                        id="hero-admin-btn"
                        style={{ padding: '0.8rem 1.6rem', fontSize: '0.95rem' }}
                    >
                        Admin Portal
                    </button>
                </div>

                <div className="hero-divider" />

                <div className="hero-stats">
                    <div>
                        <div className="hero-stat-value">RAG</div>
                        <div className="hero-stat-label">Powered Engine</div>
                    </div>
                    <div>
                        <div className="hero-stat-value">8</div>
                        <div className="hero-stat-label">Intent Categories</div>
                    </div>
                    <div>
                        <div className="hero-stat-value">Gemini</div>
                        <div className="hero-stat-label">AI Model</div>
                    </div>
                </div>
            </div>

            {/* Right column — feature list */}
            <div className="hero-features">
                {FEATURES.map(f => (
                    <div key={f.title} className="feature-item">
                        <div className="feature-icon">{f.icon}</div>
                        <div>
                            <div className="feature-title">{f.title}</div>
                            <div className="feature-desc">{f.desc}</div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default Home;
