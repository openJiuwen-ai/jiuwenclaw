export class QuestionCancelledError extends Error {
  constructor(message = "question cancelled") {
    super(message);
    this.name = "QuestionCancelledError";
  }
}

export class QuestionBusyError extends Error {
  constructor(message = "another question is already active") {
    super(message);
    this.name = "QuestionBusyError";
  }
}
