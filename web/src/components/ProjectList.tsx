import type { Project } from "../api/types";
import { useI18n } from "../i18n";

interface ProjectListProps {
  projects: Project[];
}

export function ProjectList({ projects }: ProjectListProps) {
  const { t, lang } = useI18n();
  const dateFormat = new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" });

  if (projects.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-20 text-center bg-white border border-dashed border-brand-100 rounded-2xl">
        <img src="/logo.png" alt="" className="shrink-0 size-20 opacity-80" />
        <p className="text-base font-medium text-stone-700">{t("home.empty")}</p>
        <p className="max-w-sm text-sm text-stone-500">{t("home.emptyHint")}</p>
      </div>
    );
  }

  return (
    <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {projects.map((project) => (
        <li key={project.id} className="flex flex-col gap-2 p-4 bg-white border border-brand-100 rounded-xl shadow-sm">
          <span className="font-medium truncate">{project.name}</span>
          <div className="flex items-center justify-between text-xs text-stone-500">
            <span>{dateFormat.format(new Date(project.created_at))}</span>
            <span className="px-2 py-0.5 text-brand-700 bg-brand-50 rounded-full">{t(`status.${project.status}`)}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
