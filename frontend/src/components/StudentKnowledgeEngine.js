import React, { useState } from 'react';
import { Send, User, ChevronLeft, LogOut, Bot } from 'lucide-react';

export default function StudentKnowledgeEngine({ onBack, onLogout }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: 'Hello! I am the Campus Knowledge Engine. How can I assist you with your academic queries today?' }
  ]);
  const [input, setInput] = useState('');

  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    
    setMessages([...messages, { role: 'user', text: input }]);
    const queryTxt = input.toLowerCase();
    setInput('');
    
    // Mock response
    setTimeout(() => {
      let responseText = `I'm still developing`;

      if (queryTxt.includes('class advisor') || queryTxt.includes('contact them')) {
        responseText = "Your class advisors for B.Tech CSE Section F, Semester 5 are: Dr. R. Karthi (r_karthi@cb.amrita.edu) and Ms. Krishna Priya G (g_krishnapriya@cb.amrita.edu).";
      } else if (queryTxt.includes('lab session') || queryTxt.includes('which days')) {
        responseText = "You have three lab sessions scheduled this week:\n\nMonday: Computer Networks Lab (23CSE302) from 2:55 PM – 3:45 PM\n\nWednesday: Machine Learning Lab (23CSE301) from 2:05 PM – 3:45 PM in CP Lab 2 (A404)\n\nThursday: Embedded Systems Lab (23CSE304) starting around 2:05 PM in the PG Lab, SF floor";
      }

      setMessages(prev => [...prev, { 
        role: 'assistant', 
        text: responseText
      }]);
    }, 1000);
  };

  return (
    <div className="flex h-screen bg-white">
      {/* Sidebar Context */}
      <div className="w-80 bg-slate-50 border-r border-slate-200 flex flex-col">
        <div className="p-4 border-b border-slate-200 flex justify-between items-center">
          <button onClick={onBack} className="text-slate-500 hover:text-slate-900 flex items-center gap-1 font-medium transition-colors">
            <ChevronLeft size={20} /> Gateway
          </button>
          <button onClick={onLogout} className="text-slate-500 hover:text-red-600 transition-colors">
            <LogOut size={18} />
          </button>
        </div>
        
        <div className="p-6 flex-1 overflow-y-auto">
          <div className="mb-6 flex items-center gap-3">
            <div className="bg-indigo-100 p-3 rounded-full text-indigo-700">
              <User size={24} />
            </div>
            <div>
              <h2 className="font-bold text-slate-900">John Doe</h2>
              <p className="text-sm text-slate-500">Student</p>
            </div>
          </div>
          
          <div className="space-y-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Session Context</h3>
            
            <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200/60 space-y-3">
              <div>
                <p className="text-xs text-slate-400 mb-1">University</p>
                <p className="font-medium text-slate-800 text-sm">State University</p>
              </div>
              <div>
                <p className="text-xs text-slate-400 mb-1">Department</p>
                <p className="font-medium text-slate-800 text-sm">Computer Science</p>
              </div>
              <div>
                <p className="text-xs text-slate-400 mb-1">Semester</p>
                <p className="font-medium text-slate-800 text-sm">Semester 6</p>
              </div>
              <div>
                <p className="text-xs text-slate-400 mb-1">Regulation</p>
                <p className="font-medium text-slate-800 text-sm">R20</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col bg-white">
        <div className="border-b border-slate-100 p-4 shadow-sm z-10 bg-white/80 backdrop-blur">
          <h2 className="font-bold text-lg text-slate-800 flex items-center gap-2">
            <Bot className="text-indigo-600" /> Academic Assistant
          </h2>
        </div>
        
        {/* Chat Log */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map((m, i) => (
            <div key={i} className={`flex gap-4 max-w-3xl mx-auto ${m.role === 'user' ? 'justify-end' : ''}`}>
               {m.role === 'assistant' && (
                  <div className="w-8 h-8 rounded bg-indigo-600 flex items-center justify-center flex-shrink-0 mt-1">
                    <Bot size={18} className="text-white" />
                  </div>
               )}
               <div className={`px-5 py-3.5 rounded-2xl ${m.role === 'user' ? 'bg-slate-100 text-slate-900 rounded-tr-sm' : 'bg-indigo-50 text-indigo-900 rounded-tl-sm'}`}>
                 <p className="whitespace-pre-wrap leading-relaxed">{m.text}</p>
               </div>
            </div>
          ))}
        </div>

        {/* Input Area */}
        <div className="p-6 bg-white border-t border-slate-100">
          <form onSubmit={handleSend} className="max-w-3xl mx-auto relative">
            <input 
              type="text" 
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Ask about syllabus, pyqs, and timetables..."
              className="w-full bg-slate-50 border border-slate-200 rounded-2xl pl-5 pr-14 py-4 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all shadow-sm"
            />
            <button 
              type="submit" 
              className="absolute right-3 top-3 p-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl transition-colors shadow"
            >
              <Send size={18} />
            </button>
          </form>
          <p className="text-center text-xs text-slate-400 mt-3 flex items-center justify-center gap-1">
            Engine can make mistakes. Check official sources.
          </p>
        </div>
      </div>
    </div>
  );
}
