/**
 * 运行环境与常量。
 * 保留旧版全部环境变量名，部署脚本无需改动。
 */
import * as fs from 'node:fs';
import * as path from 'node:path';

export const PORT = Number(process.env.PORT || 8104);
export const HOST = process.env.HOST || '0.0.0.0';
export const BASE_PATH = String(process.env.BASE_PATH || '').replace(/\/+$/, '');

/**
 * 兼容 site-deploy 的发布目录布局：站点根下有 releases/ 时，数据落在同级 data/。
 * 这样每次发新 release 都不会丢数据。
 */
function findDataRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 6; i += 1) {
    try {
      if (fs.statSync(path.join(dir, 'releases')).isDirectory()) return path.join(dir, 'data');
    } catch {
      /* 继续向上找 */
    }
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return path.join(__dirname, '..', 'data');
}

export const DATA_DIR = process.env.CLASSFIX_API_DATA || findDataRoot();
export const DB_FILE = path.join(DATA_DIR, 'db.json');
export const CONFIG_FILE = path.join(DATA_DIR, 'config.json');

/** 管理页静态资源目录（前端构建产物），随包发布。 */
export const ADMIN_DIST = process.env.CLASSFIX_ADMIN_DIST || path.join(__dirname, '..', 'admin');
