import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const AdminLogin = () => {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const navigate = useNavigate();
    const { login } = useAuth();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        try {
            const response = await Api.adminLogin(username, password);
            login(response.access_token, 'admin');
            navigate('/admin/portal');
        } catch (err) {
            setError(err.message);
        }
    };

    return (
        <div className="section active">
            <div className="card">
                <h2>Admin Portal Login</h2>
                <form onSubmit={handleSubmit}>
                    <input 
                        type="text" 
                        placeholder="Username" 
                        required 
                        value={username} 
                        onChange={(e) => setUsername(e.target.value)} 
                    />
                    <input 
                        type="password" 
                        placeholder="Password" 
                        required 
                        value={password} 
                        onChange={(e) => setPassword(e.target.value)} 
                    />
                    <button type="submit" className="btn-primary full-width">Login as Admin</button>
                </form>
                {error && <div className="message error">{error}</div>}
            </div>
        </div>
    );
};

export default AdminLogin;
