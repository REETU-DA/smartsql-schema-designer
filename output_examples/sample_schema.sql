-- SmartSQL Schema Designer · PostgreSQL DDL
-- Generated from reviewed source metadata. This creates tables only; it does not load data.
-- Review validation_report.csv before importing source rows.

CREATE TABLE "public"."courses" (
    "course_id" TEXT,
    "course_name" TEXT,
    "fee" NUMERIC(6,2),
    CONSTRAINT "pk_courses" PRIMARY KEY ("course_id")
);

CREATE TABLE "public"."students" (
    "student_id" TEXT,
    "student_name" TEXT,
    "email" TEXT,
    "city" TEXT,
    "admission_date" DATE,
    CONSTRAINT "pk_students" PRIMARY KEY ("student_id")
);

CREATE TABLE "public"."enrollments" (
    "enrollment_id" TEXT,
    "student_id" TEXT,
    "course_id" TEXT,
    "enrolled_on" DATE,
    "status" TEXT,
    CONSTRAINT "pk_enrollments" PRIMARY KEY ("enrollment_id")
);

-- Foreign keys are added after all tables exist, so cyclic references are supported.
ALTER TABLE "public"."enrollments" ADD CONSTRAINT "fk_enrollments_student_id_students_student_id" FOREIGN KEY ("student_id") REFERENCES "public"."students" ("student_id");
ALTER TABLE "public"."enrollments" ADD CONSTRAINT "fk_enrollments_course_id_courses_course_id" FOREIGN KEY ("course_id") REFERENCES "public"."courses" ("course_id");
