--
-- PostgreSQL database dump
--

\restrict IcWTppS20QBh29MjmdcoSu2qXK1eg71IWwt09l6WIuihBvf4WBMDg86GBfjrniB

-- Dumped from database version 15.16
-- Dumped by pg_dump version 15.16

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: OperationStatus; Type: TYPE; Schema: public; Owner: gateway
--

CREATE TYPE public."OperationStatus" AS ENUM (
    'PENDING',
    'VALIDATING',
    'VALIDATED',
    'APPROVAL_PENDING',
    'PROCESSING',
    'SUCCESS',
    'FAILED',
    'RETRYING',
    'DLQ'
);


ALTER TYPE public."OperationStatus" OWNER TO gateway;

--
-- Name: OperationType; Type: TYPE; Schema: public; Owner: gateway
--

CREATE TYPE public."OperationType" AS ENUM (
    'CREATE_USER',
    'UPDATE_USER',
    'DELETE_USER',
    'CREATE_ROLE',
    'UPDATE_ROLE',
    'DELETE_ROLE',
    'ASSIGN_ROLE',
    'REVOKE_ROLE'
);


ALTER TYPE public."OperationType" OWNER TO gateway;

--
-- Name: TargetService; Type: TYPE; Schema: public; Owner: gateway
--

CREATE TYPE public."TargetService" AS ENUM (
    'MYSQL',
    'POSTGRESQL',
    'ODOO',
    'LDAP'
);


ALTER TYPE public."TargetService" OWNER TO gateway;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: AuditLog; Type: TABLE; Schema: public; Owner: gateway
--

CREATE TABLE public."AuditLog" (
    id text NOT NULL,
    operation_id text NOT NULL,
    event_type text NOT NULL,
    old_status public."OperationStatus",
    new_status public."OperationStatus",
    message text,
    metadata jsonb,
    actor_type text,
    actor_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public."AuditLog" OWNER TO gateway;

--
-- Name: DeadLetterQueue; Type: TABLE; Schema: public; Owner: gateway
--

CREATE TABLE public."DeadLetterQueue" (
    id text NOT NULL,
    operation_id text,
    original_message jsonb NOT NULL,
    broker_message_id text,
    error_type text NOT NULL,
    error_message text NOT NULL,
    error_stacktrace text,
    retry_count integer NOT NULL,
    resolved boolean DEFAULT false NOT NULL,
    resolved_at timestamp(3) without time zone,
    resolution_notes text,
    resolved_by text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public."DeadLetterQueue" OWNER TO gateway;

--
-- Name: ProvisioningOperation; Type: TABLE; Schema: public; Owner: gateway
--

CREATE TABLE public."ProvisioningOperation" (
    id text NOT NULL,
    midpoint_request_id text,
    broker_message_id text,
    operation_type public."OperationType" NOT NULL,
    target_service public."TargetService" NOT NULL,
    status public."OperationStatus" DEFAULT 'PENDING'::public."OperationStatus" NOT NULL,
    user_data jsonb NOT NULL,
    original_message jsonb NOT NULL,
    validation_request_id text,
    validation_response jsonb,
    validated_at timestamp(3) without time zone,
    processing_started_at timestamp(3) without time zone,
    processing_completed_at timestamp(3) without time zone,
    provisioning_result jsonb,
    retry_count integer DEFAULT 0 NOT NULL,
    max_retries integer DEFAULT 3 NOT NULL,
    next_retry_at timestamp(3) without time zone,
    error_message text,
    error_stacktrace text,
    sent_to_dlq_at timestamp(3) without time zone,
    notification_sent boolean DEFAULT false NOT NULL,
    notification_sent_at timestamp(3) without time zone,
    approval_request_id text,
    approval_requested_at timestamp(3) without time zone,
    approval_response jsonb,
    approved_at timestamp(3) without time zone,
    approved_by text,
    approval_reason text,
    approval_timeout_at timestamp(3) without time zone,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


ALTER TABLE public."ProvisioningOperation" OWNER TO gateway;

--
-- Name: SystemMetrics; Type: TABLE; Schema: public; Owner: gateway
--

CREATE TABLE public."SystemMetrics" (
    id text NOT NULL,
    total_operations integer NOT NULL,
    pending_operations integer NOT NULL,
    processing_operations integer NOT NULL,
    success_operations integer NOT NULL,
    failed_operations integer NOT NULL,
    dlq_count integer NOT NULL,
    avg_processing_time_ms integer,
    max_processing_time_ms integer,
    min_processing_time_ms integer,
    messages_consumed integer,
    messages_failed integer,
    snapshot_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public."SystemMetrics" OWNER TO gateway;

--
-- Name: AuditLog AuditLog_pkey; Type: CONSTRAINT; Schema: public; Owner: gateway
--

ALTER TABLE ONLY public."AuditLog"
    ADD CONSTRAINT "AuditLog_pkey" PRIMARY KEY (id);


--
-- Name: DeadLetterQueue DeadLetterQueue_pkey; Type: CONSTRAINT; Schema: public; Owner: gateway
--

ALTER TABLE ONLY public."DeadLetterQueue"
    ADD CONSTRAINT "DeadLetterQueue_pkey" PRIMARY KEY (id);


--
-- Name: ProvisioningOperation ProvisioningOperation_pkey; Type: CONSTRAINT; Schema: public; Owner: gateway
--

ALTER TABLE ONLY public."ProvisioningOperation"
    ADD CONSTRAINT "ProvisioningOperation_pkey" PRIMARY KEY (id);


--
-- Name: SystemMetrics SystemMetrics_pkey; Type: CONSTRAINT; Schema: public; Owner: gateway
--

ALTER TABLE ONLY public."SystemMetrics"
    ADD CONSTRAINT "SystemMetrics_pkey" PRIMARY KEY (id);


--
-- Name: AuditLog_created_at_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "AuditLog_created_at_idx" ON public."AuditLog" USING btree (created_at);


--
-- Name: AuditLog_event_type_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "AuditLog_event_type_idx" ON public."AuditLog" USING btree (event_type);


--
-- Name: AuditLog_operation_id_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "AuditLog_operation_id_idx" ON public."AuditLog" USING btree (operation_id);


--
-- Name: DeadLetterQueue_created_at_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "DeadLetterQueue_created_at_idx" ON public."DeadLetterQueue" USING btree (created_at);


--
-- Name: DeadLetterQueue_operation_id_key; Type: INDEX; Schema: public; Owner: gateway
--

CREATE UNIQUE INDEX "DeadLetterQueue_operation_id_key" ON public."DeadLetterQueue" USING btree (operation_id);


--
-- Name: DeadLetterQueue_resolved_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "DeadLetterQueue_resolved_idx" ON public."DeadLetterQueue" USING btree (resolved);


--
-- Name: ProvisioningOperation_broker_message_id_key; Type: INDEX; Schema: public; Owner: gateway
--

CREATE UNIQUE INDEX "ProvisioningOperation_broker_message_id_key" ON public."ProvisioningOperation" USING btree (broker_message_id);


--
-- Name: ProvisioningOperation_created_at_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "ProvisioningOperation_created_at_idx" ON public."ProvisioningOperation" USING btree (created_at);


--
-- Name: ProvisioningOperation_midpoint_request_id_key; Type: INDEX; Schema: public; Owner: gateway
--

CREATE UNIQUE INDEX "ProvisioningOperation_midpoint_request_id_key" ON public."ProvisioningOperation" USING btree (midpoint_request_id);


--
-- Name: ProvisioningOperation_next_retry_at_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "ProvisioningOperation_next_retry_at_idx" ON public."ProvisioningOperation" USING btree (next_retry_at);


--
-- Name: ProvisioningOperation_operation_type_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "ProvisioningOperation_operation_type_idx" ON public."ProvisioningOperation" USING btree (operation_type);


--
-- Name: ProvisioningOperation_status_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "ProvisioningOperation_status_idx" ON public."ProvisioningOperation" USING btree (status);


--
-- Name: ProvisioningOperation_target_service_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "ProvisioningOperation_target_service_idx" ON public."ProvisioningOperation" USING btree (target_service);


--
-- Name: SystemMetrics_snapshot_at_idx; Type: INDEX; Schema: public; Owner: gateway
--

CREATE INDEX "SystemMetrics_snapshot_at_idx" ON public."SystemMetrics" USING btree (snapshot_at);


--
-- Name: AuditLog AuditLog_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gateway
--

ALTER TABLE ONLY public."AuditLog"
    ADD CONSTRAINT "AuditLog_operation_id_fkey" FOREIGN KEY (operation_id) REFERENCES public."ProvisioningOperation"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict IcWTppS20QBh29MjmdcoSu2qXK1eg71IWwt09l6WIuihBvf4WBMDg86GBfjrniB

