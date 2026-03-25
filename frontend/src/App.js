import React, { useState } from 'react';
import LoginScreen from './components/LoginScreen';
import GatewayScreen from './components/GatewayScreen';
import StudentKnowledgeEngine from './components/StudentKnowledgeEngine';
import StudentLostAndFound from './components/StudentLostAndFound';
import AdminKnowledgeEngine from './components/AdminKnowledgeEngine';
import AdminLostAndFound from './components/AdminLostAndFound';

function App() {
  // view can be: login, gateway, student-knowledge, admin-knowledge, student-lostfound, admin-lostfound
  const [currentView, setCurrentView] = useState('login');
  
  // role can be: student, admin
  const [userRole, setUserRole] = useState('student');

  const handleLogin = (role) => {
    setUserRole(role);
    setCurrentView('gateway');
  };

  const navigateTo = (view) => {
    setCurrentView(view);
  };

  const handleLogout = () => {
    setUserRole('student');
    setCurrentView('login');
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans">
      {/* Dynamic View Rendering */}
      {currentView === 'login' && <LoginScreen onLogin={handleLogin} />}
      
      {currentView === 'gateway' && 
        <GatewayScreen 
          role={userRole} 
          onNavigate={navigateTo} 
          onLogout={handleLogout} 
        />
      }

      {currentView === 'student-knowledge' && <StudentKnowledgeEngine onBack={() => navigateTo('gateway')} onLogout={handleLogout} />}
      {currentView === 'student-lostfound' && <StudentLostAndFound onBack={() => navigateTo('gateway')} onLogout={handleLogout} />}
      
      {currentView === 'admin-knowledge' && <AdminKnowledgeEngine onBack={() => navigateTo('gateway')} onLogout={handleLogout} />}
      {currentView === 'admin-lostfound' && <AdminLostAndFound onBack={() => navigateTo('gateway')} onLogout={handleLogout} />}
    </div>
  );
}

export default App;
