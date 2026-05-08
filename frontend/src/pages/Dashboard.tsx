import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
// @ts-ignore
import ColorBends from '../components/ColorBends';
import { Mic, Scale, LineChart } from 'lucide-react';

const buttons = [
  {
    title: 'Voice Task App',
    description: 'Natural language voice-first task management',
    path: '/voice-task',
    icon: <Mic className="w-8 h-8 mb-4 text-[#ff5c7a]" />
  },
  {
    title: 'Legal Chatbot',
    description: 'RAG Chatbot over Bharatiya Nyaya Sanhita (BNS)',
    path: '/legal-chat',
    icon: <Scale className="w-8 h-8 mb-4 text-[#00ffd1]" />
  },
  {
    title: 'Analysis',
    description: 'Analytics dashboard & KPIs',
    path: '/analysis',
    icon: <LineChart className="w-8 h-8 mb-4 text-[#8a5cff]" />
  }
];

export default function Dashboard() {
  return (
    <div className="w-full h-screen relative overflow-hidden bg-black flex items-center justify-center">
      {/* Background Animation */}
      <div className="absolute inset-0 z-0">
        <ColorBends
          colors={["#ff5c7a", "#8a5cff", "#00ffd1"]}
          rotation={90}
          speed={0.2}
          scale={1}
          frequency={1}
          warpStrength={1}
          mouseInfluence={1}
          noise={0.15}
          parallax={0.5}
          iterations={1}
          intensity={1.5}
          bandWidth={6}
          transparent
          autoRotate={0}
        />
      </div>

      {/* Content */}
      <div className="relative z-10 w-full max-w-6xl px-6 flex flex-col items-center justify-center">
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8 }}
          className="text-center mb-16"
        >
          <h1 className="text-5xl md:text-7xl font-bold tracking-tighter mb-4 text-white">
            Project Dashboard
          </h1>
          <p className="text-xl text-white/70 max-w-2xl mx-auto">
            Choose an application to continue
          </p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 w-full">
          {buttons.map((btn, index) => (
            <motion.div
              key={btn.title}
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: index * 0.15 }}
            >
              <Link to={btn.path} className="block group h-full">
                <div className="relative overflow-hidden rounded-2xl bg-white/[0.08] border border-white/20 shadow-[inset_0_1px_0_rgba(255,255,255,0.3),0_22px_45px_rgba(9,5,30,0.5)] p-12 min-h-[280px] flex flex-col justify-center backdrop-blur-2xl transition-all duration-300 hover:bg-white/[0.12] hover:-translate-y-2 hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.4),0_28px_55px_rgba(9,5,30,0.62)]">
                  <div className="absolute inset-0 bg-gradient-to-br from-white/14 via-white/5 to-transparent opacity-80 group-hover:opacity-100 transition-opacity" />
                  {btn.icon}
                  <h2 className="text-3xl font-semibold text-white mb-3">{btn.title}</h2>
                  <p className="text-white/75 text-base">{btn.description}</p>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  );
}
