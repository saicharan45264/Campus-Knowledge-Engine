import React, { useState } from 'react';
import { Lock, Mail, Users, ArrowRight, ArrowLeft } from 'lucide-react';

export default function LoginScreen({ onLogin }) {
  const [role, setRole] = useState('student');
  const [isLogin, setIsLogin] = useState(true);
  const [regStep, setRegStep] = useState(1); // 1 = Uni, 2 = Details
  
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  
  // Registration specific fields
  const [university, setUniversity] = useState('');
  const [fullName, setFullName] = useState('');
  const [year, setYear] = useState('');
  const [branch, setBranch] = useState('');
  const [sem, setSem] = useState('');
  const [section, setSection] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (isLogin) {
      if (email && password) onLogin(role);
    } else {
      if (regStep === 1) {
        if (university) setRegStep(2);
      } else {
        if (fullName && email && password) onLogin(role);
      }
    }
  };

  const toggleMode = () => {
    setIsLogin(!isLogin);
    setRegStep(1);
  };

  const resetRole = (newRole) => {
    setRole(newRole);
    if (newRole === 'admin') setIsLogin(true); // Admins don't register here
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-stone-100 p-4 relative overflow-hidden">
      {/* Decorative blobs */}
      <div className="absolute top-[-10%] left-[-10%] w-96 h-96 bg-rose-200 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob"></div>
      <div className="absolute bottom-[-10%] right-[-10%] w-96 h-96 bg-indigo-200 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob animation-delay-2000"></div>

      <div className={`w-full ${isLogin ? 'max-w-md' : 'max-w-xl'} bg-white rounded-[2rem] shadow-2xl overflow-hidden border border-rose-100 relative z-10 transition-all duration-500`}>
        {/* Header */}
        <div className="bg-gradient-to-br from-rose-500 to-rose-700 px-8 py-10 text-center text-white relative overflow-hidden">
          <div className="absolute inset-0 bg-white/10 [mask-image:linear-gradient(transparent,white)]"></div>
          <h1 className="text-3xl font-extrabold mb-2 relative z-10 tracking-tight">Campus Knowledge Engine</h1>
          <p className="text-rose-100 text-sm relative z-10 font-medium">
            {isLogin ? 'Sign in to access your dashboard' : (regStep === 1 ? 'Step 1: Select Your University' : 'Step 2: Student Details')}
          </p>
        </div>

        {/* Form */}
        <div className="p-8 sm:p-10">
          {isLogin && (
            <div className="flex bg-stone-100 p-1.5 rounded-xl mb-8">
              <button
                onClick={() => resetRole('student')}
                className={`flex-1 py-2.5 text-sm font-bold rounded-lg transition-all ${role === 'student' ? 'bg-white shadow-md text-rose-600' : 'text-stone-500 hover:text-stone-700'}`}
              >
                Student
              </button>
              <button
                onClick={() => resetRole('admin')}
                className={`flex-1 flex items-center justify-center gap-2 py-2.5 text-sm font-bold rounded-lg transition-all ${role === 'admin' ? 'bg-white shadow-md text-rose-600' : 'text-stone-500 hover:text-stone-700'}`}
              >
                <Users size={16} /> Admin
              </button>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-6">
            {isLogin ? (
              // LOGIN FORM
              <>
                <div className="space-y-5">
                  <div className="relative group">
                    <Mail className="absolute left-4 top-3.5 h-5 w-5 text-stone-400 group-focus-within:text-rose-500 transition-colors" />
                    <input type="email" value={email} onChange={e => setEmail(e.target.value)} className="w-full pl-12 pr-4 py-3.5 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:border-rose-500 focus:bg-white transition-all outline-none font-medium" placeholder="Email Address" required />
                  </div>
                  <div className="relative group">
                    <Lock className="absolute left-4 top-3.5 h-5 w-5 text-stone-400 group-focus-within:text-rose-500 transition-colors" />
                    <input type="password" value={password} onChange={e => setPassword(e.target.value)} className="w-full pl-12 pr-4 py-3.5 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:border-rose-500 focus:bg-white transition-all outline-none font-medium" placeholder="Password" required />
                  </div>
                </div>

                <button type="submit" className="w-full bg-rose-600 hover:bg-rose-700 text-white py-3.5 rounded-xl font-bold transition-all shadow-lg hover:shadow-rose-500/30 transform hover:-translate-y-0.5">
                  Log in as {role === 'admin' ? 'Admin' : 'Student'}
                </button>

                {role === 'student' && (
                  <p className="text-center text-sm text-stone-500 mt-6 cursor-pointer hover:text-rose-600 transition-colors" onClick={toggleMode}>
                    Don't have an account? <span className="font-bold underline decoration-rose-300 underline-offset-4">Register here</span>
                  </p>
                )}
              </>
            ) : (
              // REGISTRATION FORM
              <>
                {regStep === 1 ? (
                  <div className="space-y-6 animate-in fade-in slide-in-from-right-4">
                    <label className="block text-sm font-bold text-stone-700">Which university do you attend?</label>
                    <select required value={university} onChange={e => setUniversity(e.target.value)} className="w-full px-5 py-4 bg-stone-50 border-2 border-stone-200 rounded-xl focus:ring-4 focus:ring-rose-500/20 focus:border-rose-500 focus:bg-white transition-all outline-none font-bold text-stone-700 appearance-none cursor-pointer">
                      <option value="">Select your institution...</option>
                      <option value="State University">State University</option>
                      <option value="Tech Institute">Tech Institute</option>
                      <option value="Global Engineering College">Global Engineering College</option>
                    </select>

                    <button type="submit" className="w-full bg-rose-600 hover:bg-rose-700 text-white py-3.5 rounded-xl font-bold transition-all shadow-lg hover:shadow-rose-500/30 flex justify-center items-center gap-2">
                       Next <ArrowRight size={18} />
                    </button>
                  </div>
                ) : (
                  <div className="space-y-5 animate-in fade-in slide-in-from-right-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="col-span-2">
                        <input type="text" value={fullName} onChange={e => setFullName(e.target.value)} placeholder="Full Name" className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none" required />
                      </div>
                      <div className="col-span-1">
                        <select required value={year} onChange={e => setYear(e.target.value)} className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none">
                          <option value="">Year...</option><option value="1">1st Year</option><option value="2">2nd Year</option><option value="3">3rd Year</option><option value="4">4th Year</option>
                        </select>
                      </div>
                      <div className="col-span-1">
                        <input type="text" value={branch} onChange={e => setBranch(e.target.value)} placeholder="Branch (e.g. CSE)" className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none" required />
                      </div>
                      <div className="col-span-1">
                         <select required value={sem} onChange={e => setSem(e.target.value)} className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none">
                          <option value="">Semester...</option><option>Sem 1</option><option>Sem 2</option><option>Sem 3</option><option>Sem 4</option><option>Sem 5</option><option>Sem 6</option><option>Sem 7</option><option>Sem 8</option>
                        </select>
                      </div>
                      <div className="col-span-1">
                        <input type="text" value={section} onChange={e => setSection(e.target.value)} placeholder="Section (e.g. A)" className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none" required />
                      </div>
                      <div className="col-span-2">
                        <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Student Email" className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none" required />
                      </div>
                      <div className="col-span-2">
                        <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Create Password" className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-2 focus:ring-rose-500 focus:bg-white outline-none" required />
                      </div>
                    </div>

                    <div className="flex gap-3 pt-2">
                      <button type="button" onClick={() => setRegStep(1)} className="px-5 py-3.5 bg-stone-100 hover:bg-stone-200 text-stone-700 rounded-xl font-bold transition-all flex items-center justify-center">
                        <ArrowLeft size={18} />
                      </button>
                      <button type="submit" className="flex-1 bg-rose-600 hover:bg-rose-700 text-white py-3.5 rounded-xl font-bold transition-all shadow-lg hover:shadow-rose-500/30">
                        Complete Registration
                      </button>
                    </div>
                  </div>
                )}
                
                <p className="text-center text-sm text-stone-500 mt-6 cursor-pointer hover:text-rose-600 transition-colors" onClick={toggleMode}>
                  Already registered? <span className="font-bold underline decoration-rose-300 underline-offset-4">Log in</span>
                </p>
              </>
            )}
          </form>
        </div>
      </div>
    </div>
  );
}
