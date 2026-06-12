import React from 'react';
import { useNavigate } from 'react-router-dom';

const Home = () => {
    const navigate = useNavigate();

    return (
        <div className="section active">
            <div className="hero-content">
                <h1>Education that shapes your future</h1>
                <p>At Miranda College, we combine academic excellence with real-world experience to prepare students for successful international careers. Access our internal document base to answer your queries instantly.</p>
                <button className="btn-primary large" onClick={() => navigate('/student/login')}>
                    Explore Knowledge Base
                </button>
            </div>
            <div className="hero-image">
                <div className="blob"></div>
                {/* Fallback to simple styled div if no image */}
                <div style={{ width: '450px', height: '450px', backgroundColor: '#800000', borderRadius: '40% 60% 70% 30% / 40% 50% 60% 50%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white', fontSize: '2rem', textAlign: 'center', padding: '2rem' }}>
                    Knowledge<br/>Engine
                </div>
            </div>
        </div>
    );
};

export default Home;
