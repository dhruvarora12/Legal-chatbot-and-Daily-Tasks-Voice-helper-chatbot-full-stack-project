import { useState } from 'react';
import { Send, Bot, User, Scale, Lightbulb, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';

interface ChatMessage {
  id: number;
  role: 'user' | 'bot';
  text: string;
}

const recommendedQuestions = [
  "What does 'counterfeit' mean under the Bharatiya Nyaya Sanhita?",
  "In what situations is there no right of private defence?",
  "When does the right of private defence of the body extend to causing death?",
  "What is the definition of a 'public servant' in the Sanhita?",
  "What constitutes 'rape' under Section 63 of the BNS?"
];

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export default function LegalChatbot() {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 1, role: 'bot', text: 'Hello! I am your Bharatiya Nyaya Sanhita (BNS) legal assistant. How can I help you understand the new criminal laws today?' },
  ]);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    await processUserQuestion(input);
  };

  const processUserQuestion = async (question: string) => {
    const userText = question.trim();
    setInput('');
    
    // Add user message immediately
    const userMsg: ChatMessage = { id: Date.now(), role: 'user', text: userText };
    setMessages(prev => [...prev, userMsg]);
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE}/api/legal-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: userText, stream: false })
      });
      
      const data = await response.json();
      
      const botMsg: ChatMessage = { 
        id: Date.now() + 1, 
        role: 'bot', 
        text: data.answer || "Sorry, I received an empty response." 
      };
      setMessages(prev => [...prev, botMsg]);
    } catch (err) {
      console.error(err);
      const errorMsg: ChatMessage = { 
        id: Date.now() + 1, 
        role: 'bot', 
        text: "Error: Could not connect to the Legal AI backend." 
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="absolute inset-0 pt-6 px-6 pb-6 w-full max-w-7xl mx-auto flex flex-col md:flex-row gap-8 font-sans">
      
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        <motion.div 
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-4 mb-6"
        >
          <div className="p-3 bg-gradient-to-br from-[#00ffd1]/20 to-transparent rounded-2xl border border-[#00ffd1]/30 shadow-[0_0_30px_rgba(0,255,209,0.15)]">
            <Scale className="w-6 h-6 text-[#00ffd1]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white/95 tracking-tight">Legal Research Assistant</h1>
            <p className="text-[#00ffd1]/70 text-sm font-medium tracking-wide">BNS RAG Pipeline</p>
          </div>
        </motion.div>

        <div className="flex-grow bg-white/[0.02] backdrop-blur-3xl border border-white/5 rounded-3xl p-6 flex flex-col overflow-hidden shadow-[inset_0_1px_1px_rgba(255,255,255,0.05),0_0_40px_rgba(0,0,0,0.5)]">
          {/* Messages */}
          <div className="flex-grow overflow-y-auto pr-4 space-y-6 custom-scrollbar pb-4">
            {messages.map((msg) => (
              <motion.div 
                key={msg.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0 }}
                className={`flex gap-4 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
              >
                <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
                  msg.role === 'user' 
                    ? 'bg-white/10 border border-white/20' 
                    : 'bg-[#00ffd1]/20 border border-[#00ffd1]/40'
                }`}>
                  {msg.role === 'user' ? <User className="w-5 h-5 text-white" /> : <Bot className="w-5 h-5 text-[#00ffd1]" />}
                </div>
                <div className={`max-w-[80%] rounded-2xl p-4 text-[15px] font-light tracking-wide shadow-sm ${
                msg.role === 'user' 
                  ? 'bg-gradient-to-br from-white/10 to-white/5 border border-white/10 text-white rounded-tr-none' 
                  : 'bg-white/[0.03] border border-white/5 text-white/90 rounded-tl-none leading-relaxed'
              }`}>
                <div className="markdown-body">
                  <ReactMarkdown>{msg.text}</ReactMarkdown>
                </div>
              </div>
              </motion.div>
            ))}
            {isLoading && (
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex gap-4"
              >
                <div className="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center bg-[#00ffd1]/20 border border-[#00ffd1]/40">
                  <Bot className="w-5 h-5 text-[#00ffd1]" />
                </div>
                <div className="max-w-[80%] rounded-2xl p-4 bg-black/50 border border-white/10 text-[#00ffd1]/70 rounded-tl-none italic flex items-center gap-2">
                  Searching BNS Database <span className="animate-pulse">...</span>
                </div>
              </motion.div>
            )}
          </div>

          {/* Input Area */}
          <form onSubmit={handleSubmit} className="mt-6 relative">
            <input 
              type="text" 
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isLoading}
              placeholder="Ask a question about BNS..."
              className="w-full bg-white/[0.03] border border-white/10 focus:border-[#00ffd1]/40 focus:bg-white/[0.05] disabled:opacity-50 rounded-2xl py-4 pl-6 pr-16 text-white placeholder:text-white/30 outline-none transition-all shadow-[inset_0_2px_4px_rgba(0,0,0,0.2)] font-light tracking-wide"
            />
            <button 
              type="submit"
              disabled={isLoading || !input.trim()}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-2.5 bg-gradient-to-r from-[#00ffd1] to-[#00ccb0] hover:shadow-[0_0_20px_rgba(0,255,209,0.4)] disabled:opacity-50 disabled:hover:shadow-none text-black rounded-xl transition-all"
            >
              <Send className="w-5 h-5 ml-0.5" />
            </button>
          </form>
        </div>
      </div>

      {/* Side Panel: Recommended Questions */}
      <div className="w-full md:w-80 flex flex-col h-full pt-[88px]">
        <h2 className="text-sm uppercase tracking-widest font-semibold text-white/50 flex items-center gap-2 px-2 mb-4">
          <Lightbulb className="w-4 h-4 text-[#00ffd1]" />
          Suggested Queries
        </h2>
        <div className="flex-grow overflow-y-auto space-y-3 custom-scrollbar pr-2 pb-6">
          {recommendedQuestions.map((q, idx) => (
            <motion.div 
              key={idx}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.1 * idx }}
            >
              <button
                onClick={() => processUserQuestion(q)}
                disabled={isLoading}
                className="w-full text-left bg-white/[0.02] backdrop-blur-3xl border border-white/5 rounded-2xl p-5 transition-all duration-300 hover:bg-white/[0.05] hover:border-[#00ffd1]/30 hover:shadow-[0_8px_30px_rgba(0,255,209,0.05)] hover:-translate-y-0.5 group disabled:opacity-50"
              >
                <p className="text-white/70 text-sm leading-relaxed pr-6 relative font-light tracking-wide">
                  "{q}"
                  <ArrowRight className="w-4 h-4 text-[#00ffd1] absolute right-0 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0" />
                </p>
              </button>
            </motion.div>
          ))}
        </div>
      </div>

    </div>
  );
}
