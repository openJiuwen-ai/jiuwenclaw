export interface ResponseModel<T> {
  data: T | null;
  code: number;
  message: string;
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
