import './i18n';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { getProductName } from './utils/env';

document.title = `${getProductName()} Manager`;

ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
