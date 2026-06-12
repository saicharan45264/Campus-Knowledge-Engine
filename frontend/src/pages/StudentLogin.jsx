import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const StudentLogin = () => {
    const [isRegistering, setIsRegistering] = useState(false);
    const [formData, setFormData] = useState({
        name: '', college_mail: '', password: '', department: '',
        semester: '', section_id: '', regulation_year: '', academic_year: ''
    });
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');
    
    const navigate = useNavigate();
    const { login } = useAuth();

    const handleChange = (e) => setFormData({...formData, [e.target.name]: e.target.value});

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setSuccess('');

        try {
            if (isRegistering) {
                await Api.studentRegister({
                    ...formData,
                    semester: parseInt(formData.semester) || 1
                });
                setSuccess('Registration successful! Please login.');
                setIsRegistering(false);
            } else {
                const response = await Api.studentLogin(formData.college_mail, formData.password);
                login(response.access_token, 'student');
                navigate('/student/portal');
            }
        } catch (err) {
            setError(err.message);
        }
    };

    return (
        <div className="section active">
            <div className="card">
                <h2>{isRegistering ? 'Student Registration' : 'Student Login'}</h2>
                <form onSubmit={handleSubmit}>
                    {isRegistering && (
                        <>
                            <input name="name" placeholder="Full Name" required onChange={handleChange} />
                            <input name="department" placeholder="Department (e.g. CSE)" required onChange={handleChange} />
                            <input type="number" name="semester" placeholder="Semester (e.g. 5)" required onChange={handleChange} />
                            <input name="section_id" placeholder="Section (e.g. A)" required onChange={handleChange} />
                            <input name="regulation_year" placeholder="Regulation Year (e.g. 2021)" required onChange={handleChange} />
                            <input name="academic_year" placeholder="Academic Year (e.g. 2023-2024)" required onChange={handleChange} />
                        </>
                    )}
                    <input type="email" name="college_mail" placeholder="College Email" required onChange={handleChange} />
                    <input type="password" name="password" placeholder="Password" required onChange={handleChange} />
                    
                    <button type="submit" className="btn-primary full-width">
                        {isRegistering ? 'Register' : 'Login'}
                    </button>
                    
                    <p className="switch-form">
                        {isRegistering ? 'Already have an account? ' : 'New here? '}
                        <button type="button" className="link-button" onClick={() => setIsRegistering(!isRegistering)}>
                            {isRegistering ? 'Login' : 'Register'}
                        </button>
                    </p>
                </form>
                {error && <div className="message error">{error}</div>}
                {success && <div className="message success">{success}</div>}
            </div>
        </div>
    );
};

export default StudentLogin;
