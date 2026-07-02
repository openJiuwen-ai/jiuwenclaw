import './i18n';
import ReactDOM from 'react-dom/client';
import App from './App.tsx'
import './index.css'
import { getProductName } from './utils/env';

document.title = `${getProductName()} Enterprise`;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <App />,
)
