import React from 'react';
import { createRoot } from 'react-dom/client';
import HybridApp from './HybridApp';
import './styles.css';
import 'maplibre-gl/dist/maplibre-gl.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><HybridApp /></React.StrictMode>);
