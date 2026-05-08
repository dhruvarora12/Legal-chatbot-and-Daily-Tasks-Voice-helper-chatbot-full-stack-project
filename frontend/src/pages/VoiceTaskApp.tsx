import { useState, useEffect, useRef } from 'react';
import { Mic, CheckCircle2, Circle, Clock, XCircle, Bot, User, Send } from 'lucide-react';
import { motion } from 'framer-motion';

interface Task {
  id: number;
  title: string;
  description: string;
  dueDate: string;
  status: string;
}

interface ChatMessage {
  id: number;
  role: 'user' | 'bot';
  text: string;
}

interface VoiceActionResponse {
  action: string;
  task?: any;
  tasks?: any[];
  count?: number;
  error?: string;
}

// Ensure TypeScript knows about SpeechRecognition
declare global {
  interface Window {
    SpeechRecognition: any;
    webkitSpeechRecognition: any;
  }
}

export default function VoiceTaskApp() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [transcript, setTranscript] = useState<ChatMessage[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [manualInput, setManualInput] = useState("");
  const [mockQuestions, setMockQuestions] = useState<string[]>([]);
  
  const recognitionRef = useRef<any>(null);

  const normalizeTask = (raw: any): Task => ({
    id: Number(raw?.id ?? Date.now()),
    title: String(raw?.title ?? raw?.name ?? "Untitled Task"),
    description: String(raw?.description ?? raw?.details ?? ""),
    dueDate: String(raw?.dueDate ?? raw?.due_date ?? raw?.due ?? "No Date"),
    status: String(raw?.status ?? "pending"),
  });

  const getTaskTitle = (raw: any) => String(raw?.title ?? raw?.name ?? "Untitled Task");
  const getTaskDueDate = (raw: any) => String(raw?.dueDate ?? raw?.due_date ?? raw?.due ?? "No Date");

  const refreshTasks = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/tasks');
      const data = await response.json();
      setTasks(Array.isArray(data) ? data.map(normalizeTask) : []);
    } catch (err) {
      console.error("Error fetching tasks:", err);
    }
  };

  // Fetch tasks on load
  useEffect(() => {
    refreshTasks();
    fetch('http://localhost:8000/api/voice/mock-questions')
      .then(res => res.json())
      .then(data => setMockQuestions(data.questions ?? []))
      .catch(err => console.error("Error fetching mock questions:", err));
  }, []);

  useEffect(() => {
    // Initialize Speech Recognition
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.continuous = false;
      recognitionRef.current.interimResults = false;

      recognitionRef.current.onresult = (event: any) => {
        const text = event.results[0][0].transcript;
        handleVoiceInput(text);
      };

      recognitionRef.current.onend = () => {
        setIsRecording(false);
      };
    }
  }, []);

  const toggleRecording = () => {
    if (isRecording) {
      recognitionRef.current?.stop();
      setIsRecording(false);
    } else {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.start();
          setIsRecording(true);
        } catch (e) {
          console.error(e);
        }
      } else {
        alert("Speech Recognition not supported in this browser. Use the text input below.");
      }
    }
  };

  const handleManualSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualInput.trim()) return;
    const text = manualInput;
    setManualInput("");
    handleVoiceInput(text);
  };

  const handleVoiceInput = async (text: string) => {
    // Add user message to transcript
    const userMsg: ChatMessage = { id: Date.now(), role: 'user', text };
    setTranscript(prev => [...prev, userMsg]);

    try {
      const response = await fetch('http://localhost:8000/api/voice/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      const payload: VoiceActionResponse = await response.json();
      if (!response.ok) {
        throw new Error((payload as any).detail || "Voice action failed");
      }

      await refreshTasks();

      let botText = "Action completed.";
      if (payload.action === "created" && payload.task) {
        botText = `Task created: "${getTaskTitle(payload.task)}" (Due: ${getTaskDueDate(payload.task)})`;
      } else if (payload.action === "completed" && payload.task) {
        botText = `Marked as completed: "${getTaskTitle(payload.task)}"`;
      } else if (payload.action === "cancelled" && payload.task) {
        botText = `Cancelled: "${getTaskTitle(payload.task)}"`;
      } else if (payload.action === "delayed" && payload.task) {
        botText = `Delayed: "${getTaskTitle(payload.task)}" to ${getTaskDueDate(payload.task)}`;
      } else if (payload.action === "query") {
        botText = `Found ${payload.count ?? payload.tasks?.length ?? 0} tasks matching your query.`;
      } else if (payload.action === "ambiguous_match") {
        botText = payload.error || "Multiple matching tasks found. Please be more specific.";
      }

      setTranscript(prev => [...prev, { id: Date.now() + 1, role: 'bot', text: botText }]);

    } catch (err) {
      console.error(err);
      const botMsg: ChatMessage = { 
        id: Date.now() + 1, 
        role: 'bot', 
        text: err instanceof Error ? err.message : "Sorry, I couldn't process that voice action." 
      };
      setTranscript(prev => [...prev, botMsg]);
    }
  };

  const toggleTaskStatus = async (taskId: number, currentStatus: string) => {
    const newStatus = currentStatus === 'completed' ? 'pending' : 'completed';
    setTasks(prev => prev.map(t => t.id === taskId ? { ...t, status: newStatus } : t));
    
    try {
      await fetch(`http://localhost:8000/api/tasks/${taskId}/status?status=${newStatus}`, {
        method: 'PUT'
      });
    } catch (err) {
      console.error(err);
      // Revert on error
      setTasks(prev => prev.map(t => t.id === taskId ? { ...t, status: currentStatus } : t));
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle2 className="w-5 h-5 text-green-400" />;
      case 'delayed': return <Clock className="w-5 h-5 text-yellow-400" />;
      case 'cancelled': return <XCircle className="w-5 h-5 text-red-400" />;
      default: return <Circle className="w-5 h-5 text-white/40" />;
    }
  };

  return (
    <div className="absolute inset-0 pt-6 px-6 pb-6 w-full max-w-7xl mx-auto flex flex-col md:flex-row gap-8 font-sans">
      
      {/* Main Area: Mic & Chat */}
      <div className="flex-1 flex flex-col items-center justify-center bg-white/[0.02] backdrop-blur-3xl border border-white/5 rounded-3xl p-6 relative shadow-[inset_0_1px_1px_rgba(255,255,255,0.05),0_0_40px_rgba(0,0,0,0.5)] h-full overflow-hidden">
        
        <motion.div 
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-center mb-8 absolute top-8"
        >
          <h1 className="text-3xl font-bold mb-2 text-white">Voice Task Manager</h1>
          <p className="text-[#ff5c7a]/80 text-sm">Hold the mic and speak naturally to create tasks</p>
        </motion.div>

        {mockQuestions.length > 0 && (
          <div className="w-full max-w-4xl mt-20 mb-3 px-4 flex flex-wrap gap-2">
            {mockQuestions.slice(0, 4).map((q, idx) => (
              <button
                key={`${q}-${idx}`}
                onClick={() => handleVoiceInput(q)}
                className="text-xs px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-white/70 hover:text-white hover:border-[#ff5c7a]/45 hover:bg-[#ff5c7a]/10 transition-all"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {/* Live Chat/Transcript Area */}
        <div className="w-full max-w-2xl mt-4 flex-grow overflow-y-auto mb-8 space-y-4 custom-scrollbar px-4">
          {transcript.length === 0 && (
            <div className="h-full flex items-center justify-center text-white/30 italic">
              Try saying "Remind me to submit the quarterly report next Friday"
            </div>
          )}
          {transcript.map((msg, idx) => (
            <motion.div 
              key={msg.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                msg.role === 'user' 
                  ? 'bg-white/10 border border-white/20' 
                  : 'bg-[#ff5c7a]/20 border border-[#ff5c7a]/40'
              }`}>
                {msg.role === 'user' ? <User className="w-4 h-4 text-white" /> : <Bot className="w-4 h-4 text-[#ff5c7a]" />}
              </div>
              <div className={`max-w-[75%] rounded-2xl p-3 text-sm ${
                msg.role === 'user' 
                  ? 'bg-white/10 text-white rounded-tr-none' 
                  : 'bg-black/50 border border-white/10 text-[#ff5c7a] rounded-tl-none leading-relaxed'
              }`}>
                {msg.text}
              </div>
            </motion.div>
          ))}
          {isRecording && (
             <motion.div 
             initial={{ opacity: 0 }}
             animate={{ opacity: 1 }}
             className="flex gap-3 flex-row-reverse"
           >
             <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-white/10 border border-white/20">
               <User className="w-4 h-4 text-white" />
             </div>
             <div className="max-w-[75%] rounded-2xl p-3 text-sm bg-white/10 text-white/50 rounded-tr-none italic flex items-center gap-2">
                Listening <span className="animate-pulse">...</span>
             </div>
           </motion.div>
          )}
        </div>

        {/* Mic Button & Fallback Input */}
        <div className="w-full flex flex-col items-center mt-auto mb-2 gap-6">
          <motion.div 
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ delay: 0.1 }}
            className="relative flex justify-center items-center"
          >
            {isRecording && (
              <motion.div 
                animate={{ scale: [1, 1.5, 1], opacity: [0.5, 0, 0.5] }}
                transition={{ repeat: Infinity, duration: 1.5 }}
                className="absolute w-28 h-28 bg-[#ff5c7a]/30 rounded-full blur-xl"
              />
            )}
            <button 
              onClick={toggleRecording}
              className={`relative z-10 w-20 h-20 rounded-full flex items-center justify-center transition-all duration-300 ${
                isRecording 
                  ? 'bg-[#ff5c7a] shadow-[0_0_40px_rgba(255,92,122,0.6)]' 
                  : 'bg-black/40 border border-[#ff5c7a]/30 backdrop-blur-xl hover:bg-black/60 hover:border-[#ff5c7a]/60 hover:shadow-[0_0_20px_rgba(255,92,122,0.2)]'
              }`}
            >
              <Mic className={`w-8 h-8 ${isRecording ? 'text-white' : 'text-[#ff5c7a]'}`} />
            </button>
          </motion.div>

          <form onSubmit={handleManualSubmit} className="w-full max-w-md relative">
            <input 
              type="text" 
              value={manualInput}
              onChange={(e) => setManualInput(e.target.value)}
              placeholder="Or type your task here..."
              className="w-full bg-black/40 border border-white/10 focus:border-[#ff5c7a]/50 rounded-full py-3 pl-5 pr-12 text-white text-sm placeholder:text-white/30 outline-none transition-colors"
            />
            <button type="submit" className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 hover:bg-white/10 rounded-full text-white/50 hover:text-white transition-colors">
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      </div>

      {/* Side Panel: Recent Tasks */}
      <div className="w-full md:w-80 flex flex-col h-full space-y-4">
        <h2 className="text-xl font-semibold text-white/90 px-2 mt-2">Recent Tasks</h2>
        <div className="flex-grow overflow-y-auto space-y-3 custom-scrollbar pr-2 pb-6">
          {tasks.length === 0 && (
            <div className="text-center text-white/40 mt-10 text-sm">No tasks created yet.</div>
          )}
          {tasks.map((task, idx) => (
            <motion.div 
              key={task.id}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: idx * 0.1 }}
              className="w-full bg-black/40 backdrop-blur-xl border border-white/10 rounded-2xl p-4 flex flex-col gap-2 transition-all hover:bg-black/60 hover:border-[#ff5c7a]/50 hover:shadow-[0_4px_20px_rgba(255,92,122,0.15)] group"
            >
              <div className="flex items-start gap-3">
                <button 
                  onClick={() => toggleTaskStatus(task.id, task.status)}
                  className="mt-0.5 flex-shrink-0 transition-transform hover:scale-110 cursor-pointer"
                >
                  {getStatusIcon(task.status)}
                </button>
                <div className="flex-grow">
                  <h3 className={`text-sm font-medium ${task.status === 'completed' ? 'text-white/40 line-through' : 'text-white'}`}>
                    {task.title}
                  </h3>
                  {task.description && (
                    <p className="text-white/50 text-xs mt-1 line-clamp-2">{task.description}</p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 mt-1 pl-8">
                <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-[#ff5c7a]/10 text-[#ff5c7a] border border-[#ff5c7a]/20">
                  {task.dueDate}
                </span>
                <span className="text-[10px] font-medium text-white/40 capitalize">
                  {task.status}
                </span>
              </div>
            </motion.div>
          ))}
        </div>
      </div>

    </div>
  );
}
