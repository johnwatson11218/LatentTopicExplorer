--
-- PostgreSQL database dump
--

\restrict QZrzAnG1Eak64tEbfYBj3O0ghGon20uEIDHYq49eyrI9o8MJ0n4gWQeoSkPdmJb

-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

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
-- Name: process_document_terms_incremental(integer); Type: PROCEDURE; Schema: public; Owner: postgres
--

CREATE PROCEDURE public.process_document_terms_incremental(IN p_doc_id integer)
    LANGUAGE plpgsql
    AS $$ 
BEGIN
    -- 1. Clean up existing data for ONLY this document to allow re-processing
    DELETE FROM public.document_terms WHERE document_id = p_doc_id;
    DELETE FROM public.db_document_terms WHERE document_id = p_doc_id;
    DELETE FROM public.chunked_embedding_terms WHERE document_id = p_doc_id;

    -- 2. Create normalized chunk terms for this specific document
    INSERT INTO public.chunked_embedding_terms (id, document_id, cleaned_word)
    SELECT 
        ce.id,
        ce.document_id,
        LOWER(REGEXP_REPLACE(word, '[^a-zA-Z0-9]', '', 'g')) AS cleaned_word
    FROM public.chunked_embeddings ce
    CROSS JOIN LATERAL REGEXP_SPLIT_TO_TABLE(ce.input_text, '\s+') AS word
    WHERE ce.document_id = p_doc_id
      AND LENGTH(REGEXP_REPLACE(word, '[^a-zA-Z0-9]', '', 'g')) > 2;

    -- 3. Calculate frequencies for this document
    INSERT INTO public.db_document_terms (document_id, term_text, simple_freq)
    SELECT 
        p_doc_id,
        cet.cleaned_word,
        COUNT(*)
    FROM public.chunked_embedding_terms cet
    WHERE cet.document_id = p_doc_id
    GROUP BY cet.cleaned_word
    HAVING COUNT(*) > 1;

    -- 4. Insert new unique terms into the global terms dictionary
    -- Use ON CONFLICT to ignore terms that already exist from other documents
    INSERT INTO public.terms (term_text)
    SELECT DISTINCT term_text 
    FROM public.db_document_terms 
    WHERE document_id = p_doc_id
    ON CONFLICT (term_text) DO NOTHING;

    -- 5. Map frequencies to the term IDs for this document
    INSERT INTO public.document_terms (document_id, term_id, frequency)
    SELECT 
        ddt.document_id, 
        t.id, 
        ddt.simple_freq 
    FROM public.terms t
    JOIN public.db_document_terms ddt ON ddt.term_text = t.term_text
    WHERE ddt.document_id = p_doc_id;

EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Error in incremental term parsing for doc %: %', p_doc_id, SQLERRM;
END;
$$;


ALTER PROCEDURE public.process_document_terms_incremental(IN p_doc_id integer) OWNER TO postgres;

--
-- Name: refresh_topic_tables(); Type: PROCEDURE; Schema: public; Owner: postgres
--

CREATE PROCEDURE public.refresh_topic_tables()
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Drop tables in reverse dependency order
    DROP TABLE IF EXISTS topic_top_terms;
    DROP TABLE IF EXISTS topic_term_tfidf;
    DROP TABLE IF EXISTS term_df;
    DROP TABLE IF EXISTS term_tf;
    DROP TABLE IF EXISTS topic_terms;

    -- Recreate tables in correct dependency order
    CREATE TABLE topic_terms AS
    SELECT
        dt.term_id,
        dot.topic_id,
        COUNT(DISTINCT dt.document_id) as document_count,
        SUM(frequency) as total_frequency
    FROM document_terms dt
    JOIN document_topics dot ON dt.document_id = dot.document_id
    GROUP BY dt.term_id, dot.topic_id;

    CREATE TABLE term_tf AS
    SELECT
        topic_id,
        term_id,
        SUM(total_frequency) as term_frequency
    FROM topic_terms
    GROUP BY topic_id, term_id;

    CREATE TABLE term_df AS
    SELECT
        term_id,
        COUNT(DISTINCT topic_id) as document_frequency
    FROM topic_terms
    GROUP BY term_id;

    CREATE TABLE topic_term_tfidf AS
    SELECT
        tt.topic_id,
        tt.term_id,
        tt.term_frequency as tf,
        tdf.document_frequency as df,
        tt.term_frequency * LN( (SELECT COUNT(id) FROM topics) / GREATEST(tdf.document_frequency, 1)) as tf_idf
    FROM term_tf tt
    JOIN term_df tdf ON tt.term_id = tdf.term_id;

    CREATE TABLE topic_top_terms AS
    WITH ranked_terms AS (
        SELECT
            ttf.topic_id,
            t.term_text,
            ttf.tf_idf,
            ROW_NUMBER() OVER (PARTITION BY ttf.topic_id ORDER BY ttf.tf_idf DESC) as rank
        FROM topic_term_tfidf ttf
        JOIN terms t ON ttf.term_id = t.id
    )
    SELECT
        topic_id,
        term_text,
        tf_idf,
        rank
    FROM ranked_terms
    WHERE rank <= 5
    ORDER BY topic_id, rank;


       UPDATE topics t
    SET title = (
        SELECT STRING_AGG(term_text, '-' ORDER BY rank)
        FROM topic_top_terms ttt
        WHERE ttt.topic_id = t.id
    );

    RAISE NOTICE 'All topic tables refreshed successfully';
   
EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Error refreshing topic tables: %', SQLERRM;
END;
$$;


ALTER PROCEDURE public.refresh_topic_tables() OWNER TO postgres;

--
-- Name: simple_terms_parser(); Type: PROCEDURE; Schema: public; Owner: postgres
--

CREATE PROCEDURE public.simple_terms_parser()
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    -- Find all document IDs that have embeddings but no terms processed yet
    FOR r IN 
        SELECT DISTINCT document_id 
        FROM public.chunked_embeddings ce
        WHERE NOT EXISTS (
            SELECT 1 FROM public.document_terms dt WHERE dt.document_id = ce.document_id
        )
    LOOP
        RAISE NOTICE 'Processing terms for Document ID: %', r.document_id;
        CALL public.process_document_terms_incremental(r.document_id);
    END LOOP;

    RAISE NOTICE 'Incremental term parsing complete.';
END;
$$;


ALTER PROCEDURE public.simple_terms_parser() OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: chunked_embedding_terms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.chunked_embedding_terms (
    id integer,
    document_id integer,
    cleaned_word text
);


ALTER TABLE public.chunked_embedding_terms OWNER TO postgres;

--
-- Name: chunked_embeddings_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.chunked_embeddings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


ALTER SEQUENCE public.chunked_embeddings_id_seq OWNER TO postgres;

--
-- Name: chunked_embeddings; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.chunked_embeddings (
    id integer DEFAULT nextval('public.chunked_embeddings_id_seq'::regclass) NOT NULL,
    input_text character varying(100),
    embedding real[],
    sequence_number integer,
    document_id integer NOT NULL
);


ALTER TABLE public.chunked_embeddings OWNER TO postgres;

--
-- Name: db_document_terms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.db_document_terms (
    document_id integer,
    term_text text,
    simple_freq bigint
);


ALTER TABLE public.db_document_terms OWNER TO postgres;

--
-- Name: doc_coords; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.doc_coords (
    document_id integer,
    x real,
    y real
);


ALTER TABLE public.doc_coords OWNER TO postgres;

--
-- Name: document_terms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.document_terms (
    id integer NOT NULL,
    document_id integer,
    term_id integer,
    frequency integer DEFAULT 1 NOT NULL,
    positions integer[],
    tf_score double precision,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.document_terms OWNER TO postgres;

--
-- Name: document_terms_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.document_terms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.document_terms_id_seq OWNER TO postgres;

--
-- Name: document_terms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.document_terms_id_seq OWNED BY public.document_terms.id;


--
-- Name: document_topics; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.document_topics (
    id integer NOT NULL,
    document_id integer NOT NULL,
    topic_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.document_topics OWNER TO postgres;

--
-- Name: document_topics_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.document_topics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.document_topics_id_seq OWNER TO postgres;

--
-- Name: document_topics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.document_topics_id_seq OWNED BY public.document_topics.id;


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


ALTER SEQUENCE public.documents_id_seq OWNER TO postgres;

--
-- Name: documents; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.documents (
    id integer DEFAULT nextval('public.documents_id_seq'::regclass) NOT NULL,
    file_path text,
    raw_text text,
    embedding real[],
    date_created timestamp without time zone DEFAULT now(),
    title text
);


ALTER TABLE public.documents OWNER TO postgres;

--
-- Name: term_df; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.term_df (
    term_id integer,
    document_frequency bigint
);


ALTER TABLE public.term_df OWNER TO postgres;

--
-- Name: term_tf; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.term_tf (
    topic_id integer,
    term_id integer,
    term_frequency numeric
);


ALTER TABLE public.term_tf OWNER TO postgres;

--
-- Name: terms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.terms (
    id integer NOT NULL,
    term_text text NOT NULL,
    term_type character varying(20) DEFAULT 'word'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    is_stop_word boolean DEFAULT false
);


ALTER TABLE public.terms OWNER TO postgres;

--
-- Name: terms_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.terms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.terms_id_seq OWNER TO postgres;

--
-- Name: terms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.terms_id_seq OWNED BY public.terms.id;


--
-- Name: test; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.test (
    title text
);


ALTER TABLE public.test OWNER TO postgres;

--
-- Name: topic_term_tfidf; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.topic_term_tfidf (
    topic_id integer,
    term_id integer,
    tf numeric,
    df bigint,
    tf_idf double precision
);


ALTER TABLE public.topic_term_tfidf OWNER TO postgres;

--
-- Name: topic_terms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.topic_terms (
    term_id integer,
    topic_id integer,
    document_count bigint,
    total_frequency bigint
);


ALTER TABLE public.topic_terms OWNER TO postgres;

--
-- Name: topic_top_terms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.topic_top_terms (
    topic_id integer,
    term_text text,
    tf_idf double precision,
    rank bigint
);


ALTER TABLE public.topic_top_terms OWNER TO postgres;

--
-- Name: topics; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.topics (
    id integer NOT NULL,
    title text NOT NULL
);


ALTER TABLE public.topics OWNER TO postgres;

--
-- Name: topics_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.topics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.topics_id_seq OWNER TO postgres;

--
-- Name: topics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.topics_id_seq OWNED BY public.topics.id;


--
-- Name: document_terms id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_terms ALTER COLUMN id SET DEFAULT nextval('public.document_terms_id_seq'::regclass);


--
-- Name: document_topics id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_topics ALTER COLUMN id SET DEFAULT nextval('public.document_topics_id_seq'::regclass);


--
-- Name: terms id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.terms ALTER COLUMN id SET DEFAULT nextval('public.terms_id_seq'::regclass);


--
-- Name: topics id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.topics ALTER COLUMN id SET DEFAULT nextval('public.topics_id_seq'::regclass);


--
-- Name: chunked_embeddings chunked_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.chunked_embeddings
    ADD CONSTRAINT chunked_embeddings_pkey PRIMARY KEY (id);


--
-- Name: document_terms document_terms_document_id_term_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_terms
    ADD CONSTRAINT document_terms_document_id_term_id_key UNIQUE (document_id, term_id);


--
-- Name: document_terms document_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_terms
    ADD CONSTRAINT document_terms_pkey PRIMARY KEY (id);


--
-- Name: document_topics document_topics_document_id_topic_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_topics
    ADD CONSTRAINT document_topics_document_id_topic_id_key UNIQUE (document_id, topic_id);


--
-- Name: document_topics document_topics_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_topics
    ADD CONSTRAINT document_topics_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: terms terms_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.terms
    ADD CONSTRAINT terms_pkey PRIMARY KEY (id);


--
-- Name: terms terms_term_text_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.terms
    ADD CONSTRAINT terms_term_text_key UNIQUE (term_text);


--
-- Name: topics topics_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.topics
    ADD CONSTRAINT topics_pkey PRIMARY KEY (id);


--
-- Name: idx_document_terms_composite; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_document_terms_composite ON public.document_terms USING btree (document_id, term_id);


--
-- Name: idx_document_terms_doc; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_document_terms_doc ON public.document_terms USING btree (document_id);


--
-- Name: idx_document_terms_term; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_document_terms_term ON public.document_terms USING btree (term_id);


--
-- Name: idx_document_topics_document_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_document_topics_document_id ON public.document_topics USING btree (document_id);


--
-- Name: idx_document_topics_topic_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_document_topics_topic_id ON public.document_topics USING btree (topic_id);


--
-- Name: idx_terms_text; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_terms_text ON public.terms USING btree (term_text);


--
-- Name: chunked_embeddings chunked_embeddings_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.chunked_embeddings
    ADD CONSTRAINT chunked_embeddings_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);


--
-- Name: document_terms document_terms_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_terms
    ADD CONSTRAINT document_terms_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_terms document_terms_term_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_terms
    ADD CONSTRAINT document_terms_term_id_fkey FOREIGN KEY (term_id) REFERENCES public.terms(id) ON DELETE CASCADE;


--
-- Name: document_topics document_topics_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_topics
    ADD CONSTRAINT document_topics_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_topics document_topics_topic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.document_topics
    ADD CONSTRAINT document_topics_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.topics(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict QZrzAnG1Eak64tEbfYBj3O0ghGon20uEIDHYq49eyrI9o8MJ0n4gWQeoSkPdmJb

