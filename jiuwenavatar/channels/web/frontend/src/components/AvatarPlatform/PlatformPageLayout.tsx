/**
 * PlatformPageLayout — 分身平台页面统一布局
 */

import { ReactNode } from 'react';
import './AvatarPlatform.css';

export interface WorkflowStep {
  num: number;
  label: string;
  active?: boolean;
}

interface PlatformPageLayoutProps {
  title: string;
  subtitle: string;
  workflow?: WorkflowStep[];
  toolbar?: ReactNode;
  children: ReactNode;
}

export function PlatformPageLayout({ title, subtitle, workflow, toolbar, children }: PlatformPageLayoutProps) {
  return (
    <div className="avatar-platform">
      <div className="avatar-platform__hero">
        <div className="avatar-platform__hero-top">
          <div>
            <h1 className="avatar-platform__title">{title}</h1>
            <p className="avatar-platform__subtitle">{subtitle}</p>
          </div>
        </div>
        {workflow && workflow.length > 0 && (
          <div className="avatar-platform__workflow">
            {workflow.map((step) => (
              <span
                key={step.num}
                className={`avatar-platform__step${step.active ? ' avatar-platform__step--active' : ''}`}
              >
                <span className="avatar-platform__step-num">{step.num}</span>
                {step.label}
              </span>
            ))}
          </div>
        )}
      </div>
      {toolbar && <div className="avatar-platform__toolbar">{toolbar}</div>}
      <div className="avatar-platform__body">{children}</div>
    </div>
  );
}

export function PlatformEmpty({ icon, title, description }: { icon?: ReactNode; title: string; description: string }) {
  return (
    <div className="avatar-platform__empty">
      {icon && <div className="avatar-platform__empty-icon">{icon}</div>}
      <p className="avatar-platform__empty-title">{title}</p>
      <p className="avatar-platform__empty-desc">{description}</p>
    </div>
  );
}
