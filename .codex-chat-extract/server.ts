import express from 'express';
import path from 'path';
import { createServer } from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import { GoogleGenAI } from '@google/genai';
import { createServer as createViteServer } from 'vite';
import dotenv from 'dotenv';

dotenv.config();

// Port & Host
const PORT = 3000;
const HOST = '0.0.0.0';

// Initialize Gemini API client safely
let ai: GoogleGenAI | null = null;
if (process.env.GEMINI_API_KEY && process.env.GEMINI_API_KEY !== 'MY_GEMINI_API_KEY') {
  try {
    ai = new GoogleGenAI({
      apiKey: process.env.GEMINI_API_KEY,
      httpOptions: {
        headers: {
          'User-Agent': 'aistudio-build',
        },
      },
    });
    console.log('Gemini AI initialized successfully.');
  } catch (err) {
    console.error('Failed to initialize Gemini Client:', err);
  }
} else {
  console.warn('GEMINI_API_KEY is missing or contains placeholder. Bot replies will use local responses.');
}

const app = express();
app.use(express.json({ limit: '50mb' })); // support large image/file uploads as base64

// In-Memory Database
const userSessionMap = new Map<string, { ws: WebSocket; user: any }>();
const uploadedFiles = new Map<string, { id: string; name: string; size: number; mimeType: string; data: string }>();
let globalBlizzardMode = false;

// Prepopulated Global Messages for better UX on first load
const globalMessages: any[] = [];

// Helper to broadcast to all sockets (presence or global chats)
function broadcastToAll(payload: any) {
  const serialized = JSON.stringify(payload);
  userSessionMap.forEach((session) => {
    if (session.ws.readyState === WebSocket.OPEN) {
      session.ws.send(serialized);
    }
  });
}

// REST API Endpoints
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', onlineUsers: userSessionMap.size, blizzardMode: globalBlizzardMode });
});

// Translation Endpoint using Gemini or robust local translation fallback model
app.post('/api/translate', async (req, res) => {
  const { text, targetLang } = req.body;
  if (!text) {
    res.status(400).json({ error: 'Missing text content for translation' });
    return;
  }
  const lang = targetLang || 'English';

  try {
    if (ai) {
      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: `Translate the following text accurately into ${lang}. Provide ONLY the translated output text, without quotes or remarks:\n\n${text}`,
      });
      res.json({ translatedText: response.text?.trim() || text });
    } else {
      throw new Error("No Gemini configured");
    }
  } catch (err) {
    console.warn('Translation API fallback triggered:', (err as Error).message);
    const sampleTranslations: { [lang: string]: { [word: string]: string } } = {
      'spanish': {
        'welcome': 'Bienvenido',
        'survival': 'Supervivencia',
        'admin': 'Administrador',
        'blizzard': 'Tormenta de nieve',
        'dice rolled': 'dado lanzado',
        'hello': 'hola',
      },
      'hindi': {
        'welcome': 'स्वागत है',
        'survival': 'उत्तरजीविता',
        'admin': 'प्रशासक',
        'blizzard': 'बर्फ़ीला तूफ़ान',
        'dice rolled': 'पासा फेंका गया',
        'hello': 'नमस्ते',
      },
    };

    const targetKey = lang.toLowerCase();
    let localTranslatedText = text;

    if (sampleTranslations[targetKey]) {
      Object.entries(sampleTranslations[targetKey]).forEach(([eng, trans]) => {
        const regex = new RegExp(eng, 'gi');
        localTranslatedText = localTranslatedText.replace(regex, trans);
      });
    }

    if (localTranslatedText === text) {
      localTranslatedText = `[Translated to ${lang}]: ${text}`;
    }
    res.json({ translatedText: localTranslatedText });
  }
});

// File Upload endpoint accepting Base64 files
app.post('/api/upload', (req, res) => {
  const { name, size, mimeType, base64Data } = req.body;
  if (!name || !base64Data) {
    res.status(400).json({ error: 'Missing filename or files data' });
    return;
  }

  const id = `file-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
  uploadedFiles.set(id, { id, name, size, mimeType, data: base64Data });

  const url = `/api/files/${id}`;
  res.json({ id, name, size, mimeType, url });
});

// File Download/Stream endpoint
app.get('/api/files/:id', (req, res) => {
  const file = uploadedFiles.get(req.params.id);
  if (!file) {
    res.status(404).json({ error: 'File not found' });
    return;
  }

  try {
    const rawData = file.data.split(';base64,').pop();
    if (!rawData) {
      res.status(400).json({ error: 'Malformed base64 contents' });
      return;
    }
    const buffer = Buffer.from(rawData, 'base64');
    res.setHeader('Content-Type', file.mimeType || 'application/octet-stream');
    res.setHeader('Content-Disposition', `attachment; filename="${file.name}"`);
    res.send(buffer);
  } catch (error) {
    console.error('File stream error:', error);
    res.status(500).json({ error: 'Failed to retrieve file contents' });
  }
});

// Server boot up
async function startServer() {
  const server = createServer(app);
  const wss = new WebSocketServer({ noServer: true });

  server.on('upgrade', (request, socket, head) => {
    wss.handleUpgrade(request, socket, head, (ws) => {
      wss.emit('connection', ws, request);
    });
  });

  // Handle WebSockets integrations
  wss.on('connection', (ws) => {
    let currentUserId: string | null = null;

    ws.on('message', async (messageBuffer) => {
      try {
        const rawMessage = messageBuffer.toString();
        const data = JSON.parse(rawMessage);
        
        switch (data.type) {
          case 'init': {
            const clientUser = data.user;
            // 0. If this is a guest trying to replace an old connection, drop the old one!
            // OR if it explicitly asks to logout
            if (currentUserId && currentUserId !== clientUser.id) {
              userSessionMap.delete(currentUserId);
              broadcastToAll({ type: 'deleted_user', userId: currentUserId });
            }
            currentUserId = clientUser.id;
            
            // Map socket to active user
            userSessionMap.set(currentUserId, { ws, user: clientUser });
            console.log(`User connected: ${clientUser.name} (${currentUserId})`);

            // 1. Send all current connected users list to the initialized user
            const activeUsersList = Array.from(userSessionMap.values()).map((s) => s.user);
            // Append GeminiBot explicitly as a permanent utility bot
            activeUsersList.push({
              id: 'gemini_bot',
              name: 'WOS BOT',
              avatar: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=100&h=100&fit=crop',
              status: 'online',
              isBot: true,
            });

            ws.send(JSON.stringify({
              type: 'init_ok',
              history: globalMessages,
              users: activeUsersList,
              blizzardMode: globalBlizzardMode,
            }));

            // 2. Broadcast updated user presence to everyone
            broadcastToAll({
              type: 'presence',
              users: activeUsersList,
            });
            break;
          }

          case 'logout': {
            if (currentUserId) {
              console.log(`User explicitly logged out: ${currentUserId}`);
              userSessionMap.delete(currentUserId);
              
              broadcastToAll({ type: 'deleted_user', userId: currentUserId });
              currentUserId = null;
              
              const activeUsersList = Array.from(userSessionMap.values()).map((s) => s.user);
              activeUsersList.push({
                id: 'gemini_bot',
                name: 'WOS BOT',
                avatar: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=100&h=100&fit=crop',
                status: 'online',
                isBot: true,
              });

              broadcastToAll({
                type: 'presence',
                users: activeUsersList,
              });
            }
            break;
          }

          case 'message': {
            const msg = data.message;
            msg.reactions = msg.reactions || {};
            
            if (msg.chatId === 'global') {
              // Store global message
              globalMessages.push(msg);
              // Store up to 1000 messages maximum
              if (globalMessages.length > 1000) {
                globalMessages.shift();
              }
              // Broadcast
              broadcastToAll({ type: 'message', message: msg });
            } else if (msg.chatId === 'gemini_bot') {
              // Private message to bot
              // Send input message back to sender (echo / confirm)
              ws.send(JSON.stringify({ type: 'message', message: msg }));
              
              // Handle Smart Bot Response
              handleBotResponse(msg.senderId, msg.text, msg.senderName, msg.fileInfo);
            } else {
              // Private message to another real connected user
              const recipientSession = userSessionMap.get(msg.chatId);
              if (recipientSession) {
                recipientSession.ws.send(JSON.stringify({ type: 'message', message: msg }));
              }
              // Also send back to self to synchronize across multiple tabs of same sender
              ws.send(JSON.stringify({ type: 'message', message: msg }));
            }
            break;
          }

          case 'reaction': {
            const { messageId, emoji, userId, isAdd, chatId } = data;
            
            // Apply reaction on server-side message store (Global)
            const targetMsg = globalMessages.find((m) => m.id === messageId);
            if (targetMsg) {
              if (!targetMsg.reactions[emoji]) {
                targetMsg.reactions[emoji] = [];
              }
              if (isAdd) {
                if (!targetMsg.reactions[emoji].includes(userId)) {
                  targetMsg.reactions[emoji].push(userId);
                }
              } else {
                targetMsg.reactions[emoji] = targetMsg.reactions[emoji].filter((u: string) => u !== userId);
              }
            }

            // Broadcast reaction update to all or direct private recipient
            if (chatId === 'global') {
              broadcastToAll({ type: 'reaction', messageId, emoji, userId, isAdd });
            } else {
              const recipientSession = userSessionMap.get(chatId);
              if (recipientSession) {
                recipientSession.ws.send(JSON.stringify({ type: 'reaction', messageId, emoji, userId, isAdd }));
              }
              // Echo to sender for dynamic state sync
              ws.send(JSON.stringify({ type: 'reaction', messageId, emoji, userId, isAdd }));
            }
            break;
          }

          case 'typing': {
            const { chatId, senderId, isTyping } = data;
            if (chatId === 'global') {
              broadcastToAll({ type: 'typing', chatId, senderId, isTyping });
            } else {
              const recipientSession = userSessionMap.get(chatId);
              if (recipientSession) {
                recipientSession.ws.send(JSON.stringify({ type: 'typing', chatId: senderId, senderId, isTyping }));
              }
            }
            break;
          }

          // Video/Audio Calling Signals Relays
          case 'call:request':
          case 'call:ringing':
          case 'call:accept':
          case 'call:decline':
          case 'call:hangup': {
            const { callerId, receiverId, isVideo, callId, duration } = data;
            console.log(`Call event signal [${data.type}]: ${callerId} -> ${receiverId}`);
            
            const recipientSession = userSessionMap.get(receiverId);
            const callerSession = userSessionMap.get(callerId);

            if (recipientSession) {
              recipientSession.ws.send(JSON.stringify({
                type: data.type,
                callerId,
                receiverId,
                isVideo,
                callId,
                duration,
              }));
            }
            // For hangup/decline, notify caller as well
            if (['call:decline', 'call:hangup', 'call:accept'].includes(data.type) && callerSession) {
              callerSession.ws.send(JSON.stringify({
                type: data.type,
                callerId,
                receiverId,
                isVideo,
                callId,
                duration,
              }));
            }
            break;
          }

          case 'delete_message': {
            const { messageId, chatId } = data;
            // Remove from server store
            const index = globalMessages.findIndex((m) => m.id === messageId);
            if (index !== -1) {
              globalMessages.splice(index, 1);
            }
            // Broadcast deletion back to all users
            broadcastToAll({ type: 'delete_message', messageId, chatId });
            break;
          }

          case 'admin:blizzard': {
            const { isFrozen } = data;
            globalBlizzardMode = isFrozen;
            broadcastToAll({ type: 'admin:blizzard', isFrozen });
            break;
          }

          case 'admin:clear': {
            globalMessages.length = 0;
            broadcastToAll({ type: 'history_cleared', history: globalMessages });
            break;
          }

          case 'admin:announcement': {
            const { alertText } = data;
            broadcastToAll({ type: 'admin:announcement', alertText });
            break;
          }
        }
      } catch (err) {
        console.error('Socket message processing failed:', err);
      }
    });

    ws.on('close', () => {
      if (currentUserId) {
        userSessionMap.delete(currentUserId);
        console.log(`User disconnected: ${currentUserId}`);
        
        // Broadcast new presence list
        const activeUsersList = Array.from(userSessionMap.values()).map((s) => s.user);
        activeUsersList.push({
          id: 'gemini_bot',
          name: 'WOS BOT',
          avatar: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=100&h=100&fit=crop',
          status: 'online',
          isBot: true,
        });

        broadcastToAll({
          type: 'presence',
          users: activeUsersList,
        });
      }
    });
  });

  // Smart assistant processor using Gemini API
  async function handleBotResponse(chatId: string, userText: string, senderName: string, fileInfo?: any) {
    // Send standard "typing..." state to client instantly
    const senderWsSession = userSessionMap.get(chatId);
    
    // Typing broadcast
    if (chatId === 'global') {
      broadcastToAll({ type: 'typing', chatId: 'global', senderId: 'gemini_bot', isTyping: true });
    } else if (senderWsSession) {
      senderWsSession.ws.send(JSON.stringify({ type: 'typing', chatId: 'gemini_bot', senderId: 'gemini_bot', isTyping: true }));
    }

    // Set a small delay to simulate human-AI typing feel
    setTimeout(async () => {
      let botResponseText = '';
      
      try {
        if (ai) {
          // Prepare parts for multimedia prompt
          const parts: any[] = [];
          
          // If image is attached, append base64 image inline
          if (fileInfo && fileInfo.id && uploadedFiles.has(fileInfo.id)) {
            const actualFile = uploadedFiles.get(fileInfo.id);
            if (actualFile && actualFile.mimeType.startsWith('image/')) {
              const base64CleanData = actualFile.data.split(';base64,').pop();
              if (base64CleanData) {
                parts.push({
                  inlineData: {
                    mimeType: actualFile.mimeType,
                    data: base64CleanData,
                  }
                });
                console.log(`Sending attached image [${actualFile.name}] to Gemini Content API`);
              }
            }
          }

          // Clean up query text
          const queryText = userText.replace(/@WOSBot/gi, '').trim();
          parts.push({ text: `Question from user "${senderName}": ${queryText}` });

          const promptResult = await ai.models.generateContent({
            model: 'gemini-3.5-flash',
            contents: { parts },
            config: {
              systemInstruction: `You are @WOSBot, an official interactive assistant in a real-time tactical Web Chat. 
              Keep descriptions informative, helpful, wittily conversational, and styled in proper Markdown formats (bold, headings, bullet lists, code chunks).
              If the user has uploaded an image, welcome it and describe the image findings, explaining what it shows.
              Address the user directly by their name: ${senderName}.
              Maintain the arctic survival tactical theme and conversational tone with micro-accents (e.g. /help, /about, /quote).`,
            }
          });

          botResponseText = promptResult.text || "I was unable to extract logical content from the universe right now.";
        } else {
          // Fallback static Responses if API Key is missing or invalid
          const textLower = userText.toLowerCase();
          if (textLower.includes('help')) {
            botResponseText = `🤖 **@WOSBot Help Guide**:\n\n- Write any text and send it directly to private chat to have a witty breakdown.\n- Upload any document or image file using the 📎 button.\n- Share this browser link in another tab to call yourself or chat live!`;
          } else if (textLower.includes('/quote')) {
            const quotes = [
              "“The best way to predict the future is to invent it.” – Alan Kay",
              "“Talk is cheap. Show me the code.” – Linus Torvalds",
              "“Simplicity is the ultimate sophistication.” – Leonardo da Vinci"
            ];
            botResponseText = `📜 Here is your Quote:\n\n*${quotes[Math.floor(Math.random() * quotes.length)]}*`;
          } else if (fileInfo) {
            botResponseText = `📎 **Received File!**\n\nI received your file **${fileInfo.name}** (${(fileInfo.size / 1024).toFixed(1)} KB).\n\n*(Note: Provide a real operational 'GEMINI_API_KEY' in Settings to unlock actual Vision-based visual analyses!)*`;
          } else {
            botResponseText = `🤖 Hello, **${senderName}**!\n\nI am currently running in **Local fallback mode** because a workspace Gemini credentials key is still being configured in the Settings. \n\n*Try sending **help** or **/quote** to see simulated response engines!*`;
          }
        }
      } catch (gemError) {
        console.error('Gemini processing failed:', gemError);
        botResponseText = `🤖 **Oops! Gemini is thinking...**\n\nThere was an issue parsing that request. Detailed logs: \`${(gemError as Error).message}\``;
      }

      // Prepare response message object
      const botMessage = {
        id: `bot-msg-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
        chatId: chatId,
        senderId: 'gemini_bot',
        senderName: 'WOS BOT',
        senderAvatar: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=100&h=100&fit=crop',
        text: botResponseText,
        timestamp: new Date().toISOString(),
        type: 'text',
        reactions: {},
      };

      // Turn off chatbot typing state
      if (chatId === 'global') {
        broadcastToAll({ type: 'typing', chatId: 'global', senderId: 'gemini_bot', isTyping: false });
        globalMessages.push(botMessage);
        broadcastToAll({ type: 'message', message: botMessage });
      } else if (senderWsSession) {
        senderWsSession.ws.send(JSON.stringify({ type: 'typing', chatId: 'gemini_bot', senderId: 'gemini_bot', isTyping: false }));
        senderWsSession.ws.send(JSON.stringify({ type: 'message', message: botMessage }));
      }
    }, 1500);
  }

  // Vite Assets Integration (using standard Full-Stack template rules)
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  server.listen(PORT, HOST, () => {
    console.log(`Server successfully started on http://localhost:${PORT}`);
  });
}

startServer().catch((err) => {
  console.error('Error starting server application:', err);
});
