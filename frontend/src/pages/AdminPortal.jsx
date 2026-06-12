import React, { useState, useEffect } from 'react';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const AdminPortal = () => {
    const [activeTab, setActiveTab] = useState('upload'); // 'upload', 'insights', 'students'
    const [file, setFile] = useState(null);
    const [formData, setFormData] = useState({
        document_type: 'curriculum',
        department: '', semester: '', section_id: '', academic_year: ''
    });
    const [msg, setMsg] = useState({ text: '', type: '' });
    const { token } = useAuth();
    
    // Insights & Students state
    const [documents, setDocuments] = useState([]);
    const [loadingDocs, setLoadingDocs] = useState(false);
    const [students, setStudents] = useState([]);
    const [loadingStudents, setLoadingStudents] = useState(false);

    const handleChange = (e) => setFormData({...formData, [e.target.name]: e.target.value});

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!file) {
            setMsg({ text: 'Please select a file', type: 'error' });
            return;
        }

        const data = new FormData();
        data.append('file', file);
        Object.keys(formData).forEach(key => {
            if (formData[key]) data.append(key, formData[key]);
        });
        
        // Add hardcoded regulation_year
        data.append('regulation_year', '2023-2027');

        setMsg({ text: 'Uploading...', type: '' });

        try {
            const response = await Api.uploadDocument(data, token);
            setMsg({ text: `Upload successful! Document ID: ${response.doc_id}`, type: 'success' });
            e.target.reset();
            setFile(null);
            setFormData({ document_type: 'curriculum', department: '', semester: '', section_id: '', academic_year: '' });
            if (activeTab === 'insights') fetchDocuments();
        } catch (error) {
            setMsg({ text: error.message, type: 'error' });
        }
    };

    const fetchDocuments = async () => {
        setLoadingDocs(true);
        try {
            const res = await Api.getAdminDocuments(token);
            setDocuments(res.documents || []);
        } catch (error) {
            console.error("Failed to fetch documents", error);
            setMsg({ text: 'Failed to load documents', type: 'error' });
        } finally {
            setLoadingDocs(false);
        }
    };

    const fetchStudents = async () => {
        setLoadingStudents(true);
        try {
            const res = await Api.getAdminStudents(token);
            setStudents(res.students || []);
        } catch (error) {
            console.error("Failed to fetch students", error);
            setMsg({ text: 'Failed to load students', type: 'error' });
        } finally {
            setLoadingStudents(false);
        }
    };

    const handleDelete = async (docId) => {
        if (!window.confirm("Are you sure you want to delete this document?")) return;
        
        try {
            await Api.deleteAdminDocument(docId, token);
            setMsg({ text: 'Document deleted successfully', type: 'success' });
            fetchDocuments(); // Refresh the list
        } catch (error) {
            console.error("Failed to delete document", error);
            setMsg({ text: error.message || 'Failed to delete document', type: 'error' });
        }
    };

    useEffect(() => {
        setMsg({ text: '', type: '' }); // Clear messages when switching tabs
        if (activeTab === 'insights') fetchDocuments();
        if (activeTab === 'students') fetchStudents();
    }, [activeTab]);

    return (
        <div className="section active">
            <div className="portal-container" style={{ maxWidth: '1000px', margin: '0 auto' }}>
                <div style={{ display: 'flex', gap: '1rem', marginBottom: '2rem' }}>
                    <button 
                        className={activeTab === 'upload' ? 'btn-primary' : 'btn-secondary'} 
                        onClick={() => setActiveTab('upload')}
                    >
                        Upload Documents
                    </button>
                    <button 
                        className={activeTab === 'insights' ? 'btn-primary' : 'btn-secondary'} 
                        onClick={() => setActiveTab('insights')}
                    >
                        Insights
                    </button>
                    <button 
                        className={activeTab === 'students' ? 'btn-primary' : 'btn-secondary'} 
                        onClick={() => setActiveTab('students')}
                    >
                        Students
                    </button>
                </div>

                {msg.text && <div className={`message ${msg.type}`} style={{ marginBottom: '1rem' }}>{msg.text}</div>}

                {activeTab === 'upload' && (
                    <>
                        <h2>Upload Documents</h2>
                        <form onSubmit={handleSubmit} className="upload-form card" style={{ maxWidth: '800px' }}>
                            <div className="form-group">
                                <label>Document File (PDF)</label>
                                <input type="file" accept=".pdf" required onChange={(e) => setFile(e.target.files[0])} />
                            </div>
                            <div className="form-group">
                                <label>Document Type</label>
                                <select name="document_type" value={formData.document_type} onChange={handleChange} required>
                                    <option value="curriculum">Curriculum</option>
                                    <option value="policy">Policy</option>
                                    <option value="timetable">Timetable</option>
                                    <option value="academic_calendar">Academic Calendar</option>
                                    <option value="regulations">Regulations</option>
                                    <option value="other">Other</option>
                                </select>
                            </div>
                            {/* Conditional Rendering based on Document Type */}
                            {formData.document_type === 'curriculum' && (
                                <>
                                    <div className="form-group"><label>Department</label><input name="department" placeholder="e.g. CSE" onChange={handleChange} value={formData.department} required /></div>
                                </>
                            )}
                            {formData.document_type === 'timetable' && (
                                <>
                                    <div className="form-group"><label>Department</label><input name="department" placeholder="e.g. CSE" onChange={handleChange} value={formData.department} required /></div>
                                    <div className="form-group"><label>Section</label><input name="section_id" placeholder="e.g. A" onChange={handleChange} value={formData.section_id} required /></div>
                                    <div className="form-group"><label>Semester (Optional)</label><input type="number" name="semester" placeholder="e.g. 5" onChange={handleChange} value={formData.semester} /></div>
                                </>
                            )}
                            {/* For policy or other, we might want to show optional fields or hide them. The user didn't specify. Hiding for now to keep it clean. */}
                            
                            <button type="submit" className="btn-primary full-width">Upload Document</button>
                        </form>
                    </>
                )}

                {activeTab === 'insights' && (
                    <>
                        <h2>Uploaded Documents Insights</h2>
                        <div className="card" style={{ overflowX: 'auto', maxWidth: '100%' }}>
                            {loadingDocs ? (
                                <p>Loading documents...</p>
                            ) : documents.length === 0 ? (
                                <p>No documents uploaded yet.</p>
                            ) : (
                                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid #ddd' }}>
                                            <th style={{ padding: '10px' }}>Type</th>
                                            <th style={{ padding: '10px' }}>Context</th>
                                            <th style={{ padding: '10px' }}>Status</th>
                                            <th style={{ padding: '10px' }}>Uploaded At</th>
                                            <th style={{ padding: '10px' }}>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {documents.map(doc => (
                                            <tr key={doc.id} style={{ borderBottom: '1px solid #eee' }}>
                                                <td style={{ padding: '10px' }}><strong>{doc.document_type}</strong></td>
                                                <td style={{ padding: '10px', fontSize: '0.9em', color: '#555' }}>
                                                    {doc.department && <span>Dept: {doc.department} <br/></span>}
                                                    {doc.semester && <span>Sem: {doc.semester} <br/></span>}
                                                    {doc.section_id && <span>Sec: {doc.section_id} <br/></span>}
                                                    {doc.regulation_year && <span>Reg: {doc.regulation_year} <br/></span>}
                                                    {doc.academic_year && <span>AcYear: {doc.academic_year}</span>}
                                                </td>
                                                <td style={{ padding: '10px' }}>
                                                    <span style={{ 
                                                        padding: '3px 8px', 
                                                        borderRadius: '12px', 
                                                        fontSize: '0.85em',
                                                        backgroundColor: doc.status === 'indexed' ? '#e6f4ea' : doc.status.startsWith('error') ? '#fce8e6' : '#fef7e0',
                                                        color: doc.status === 'indexed' ? '#137333' : doc.status.startsWith('error') ? '#c5221f' : '#b06000'
                                                    }}>
                                                        {doc.status}
                                                    </span>
                                                </td>
                                                <td style={{ padding: '10px', fontSize: '0.9em' }}>
                                                    {new Date(doc.uploaded_at).toLocaleString()}
                                                </td>
                                                <td style={{ padding: '10px' }}>
                                                    <button 
                                                        onClick={() => handleDelete(doc.id)}
                                                        style={{ 
                                                            background: '#dc3545', color: 'white', border: 'none', 
                                                            padding: '5px 10px', borderRadius: '4px', cursor: 'pointer' 
                                                        }}
                                                    >
                                                        Delete
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    </>
                )}

                {activeTab === 'students' && (
                    <>
                        <h2>Registered Students</h2>
                        <div className="card" style={{ overflowX: 'auto', maxWidth: '100%' }}>
                            {loadingStudents ? (
                                <p>Loading students...</p>
                            ) : students.length === 0 ? (
                                <p>No students registered yet.</p>
                            ) : (
                                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid #ddd' }}>
                                            <th style={{ padding: '10px' }}>Name</th>
                                            <th style={{ padding: '10px' }}>Email</th>
                                            <th style={{ padding: '10px' }}>Department</th>
                                            <th style={{ padding: '10px' }}>Academic Info</th>
                                            <th style={{ padding: '10px' }}>Registered At</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {students.map(student => (
                                            <tr key={student.id} style={{ borderBottom: '1px solid #eee' }}>
                                                <td style={{ padding: '10px' }}><strong>{student.name}</strong></td>
                                                <td style={{ padding: '10px' }}>{student.college_mail}</td>
                                                <td style={{ padding: '10px' }}>{student.department}</td>
                                                <td style={{ padding: '10px', fontSize: '0.9em', color: '#555' }}>
                                                    Sem: {student.semester} <br/>
                                                    Sec: {student.section_id} <br/>
                                                    Reg: {student.regulation_year} <br/>
                                                    AcYear: {student.academic_year}
                                                </td>
                                                <td style={{ padding: '10px', fontSize: '0.9em' }}>
                                                    {new Date(student.created_at).toLocaleDateString()}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
};

export default AdminPortal;
