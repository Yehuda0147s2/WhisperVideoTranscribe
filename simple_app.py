import streamlit as st
import os
import tempfile
import shutil
from pathlib import Path
import traceback
import time
import uuid

# Import custom modules
from modules.video_processor import VideoProcessor
from modules.transcription import TranscriptionService
from modules.translation import TranslationService
from modules.subtitle_handler import SubtitleHandler
from modules.utils import get_video_info, format_duration
from modules.database import get_database_manager

# Initialize services
@st.cache_resource
def init_services():
    """Initialize all services with caching"""
    return {
        'video_processor': VideoProcessor(),
        'transcription': TranscriptionService(),
        'translation': TranslationService(),
        'subtitle_handler': SubtitleHandler()
    }

def validate_file_simple(file_data, max_size_mb=100):
    """Simple file validation"""
    if not file_data:
        return False, "No file data"
    
    file_size_mb = len(file_data) / (1024 * 1024)
    if file_size_mb > max_size_mb:
        return False, f"File too large: {file_size_mb:.1f}MB > {max_size_mb}MB"
    
    return True, f"File size: {file_size_mb:.1f}MB"

def main():
    st.set_page_config(
        page_title="YoungKush V.AI - Video Subtitle Generator",
        page_icon="🎬",
        layout="wide"
    )
    
    st.title("🎬 YoungKush V.AI - Video Subtitle Generator")
    st.markdown("Upload a video, generate subtitles with AI transcription, and translate them!")
    
    # Initialize services and database
    services = init_services()
    db_manager = get_database_manager()
    
    if db_manager is None:
        st.error("Database connection failed. Some features may not work properly.")
    
    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        source_language = st.selectbox(
            "Source Language",
            ["auto", "en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh"],
            help="Language of the video audio"
        )
        
        target_language = st.selectbox(
            "Target Language for Translation",
            ["none", "en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh"],
            help="Leave as 'none' to skip translation"
        )
        
        st.subheader("🎨 Subtitle Styling")
        font_size = st.slider("Font Size", 12, 48, 24)
        font_color = st.color_picker("Font Color", "#FFFFFF")
        outline_color = st.color_picker("Outline Color", "#000000")
        
        with st.expander("Advanced Options"):
            max_file_size = st.number_input("Max File Size (MB)", 1, 100, 50)
            chunk_length = st.number_input("Audio Chunk Length (seconds)", 10, 60, 30)
    
    # Main content area
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.header("📁 Upload Video")
        
        # Simple file uploader without size restrictions
        uploaded_file = st.file_uploader(
            "Choose a video file",
            type=['mp4', 'avi', 'mov', 'mkv', 'wmv'],
            help="Upload your video file for processing"
        )
        
        if uploaded_file is not None:
            # Get file data
            file_data = uploaded_file.getvalue()
            
            # Validate
            is_valid, msg = validate_file_simple(file_data, max_file_size)
            
            if not is_valid:
                st.error(f"❌ {msg}")
            else:
                st.success(f"✅ Video uploaded successfully!")
                st.info(f"📊 {msg}")
                
                # Store file in session state for processing
                if 'video_data' not in st.session_state:
                    st.session_state.video_data = {}
                
                st.session_state.video_data = {
                    'file_data': file_data,
                    'filename': uploaded_file.name,
                    'ready': True
                }
                
                # Show file info
                st.json({
                    "Filename": uploaded_file.name,
                    "Size": f"{len(file_data) / (1024*1024):.1f} MB",
                    "Type": uploaded_file.type or "video"
                })
    
    with col2:
        st.header("🎵 Processing")
        
        if 'video_data' in st.session_state and st.session_state.video_data.get('ready'):
            if st.button("🚀 Start Processing", type="primary"):
                
                # Create temporary workspace
                session_id = str(uuid.uuid4())[:8]
                temp_dir = Path("temp") / session_id
                temp_dir.mkdir(parents=True, exist_ok=True)
                
                start_time = time.time()
                job_id = None
                
                try:
                    # Save uploaded file
                    video_data = st.session_state.video_data
                    temp_video_path = temp_dir / f"input_{video_data['filename']}"
                    
                    with open(temp_video_path, "wb") as f:
                        f.write(video_data['file_data'])
                    
                    # Create database job if available
                    if db_manager:
                        job_id = db_manager.create_video_job(
                            filename=video_data['filename'],
                            file_size=len(video_data['file_data']) / (1024*1024),
                            source_language=source_language if source_language != "auto" else None,
                            target_language=target_language if target_language != "none" else None,
                            font_size=font_size,
                            font_color=font_color,
                            outline_color=outline_color
                        )
                        db_manager.update_job_status(job_id, 'processing')
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    # Step 1: Extract audio
                    status_text.text("🎵 Extracting audio from video...")
                    progress_bar.progress(10)
                    
                    audio_path = services['video_processor'].extract_audio(
                        str(temp_video_path),
                        str(temp_dir / "audio.wav")
                    )
                    
                    # Step 2: Transcribe audio
                    status_text.text("🎤 Transcribing audio with Whisper AI...")
                    progress_bar.progress(30)
                    
                    segments = services['transcription'].transcribe_audio(
                        audio_path,
                        language=source_language if source_language != "auto" else None,
                        chunk_length=chunk_length
                    )
                    
                    if not segments:
                        st.error("❌ No speech detected in the video")
                        if db_manager and job_id:
                            db_manager.update_job_status(job_id, 'failed', 'No speech detected')
                        return
                    
                    # Step 3: Generate SRT subtitles
                    status_text.text("📝 Generating subtitles...")
                    progress_bar.progress(50)
                    
                    srt_content = services['subtitle_handler'].generate_srt(segments)
                    srt_path = temp_dir / "subtitles.srt"
                    
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(srt_content)
                    
                    # Step 4: Translation (if requested)
                    translated_srt_path = srt_path
                    if target_language != "none":
                        status_text.text(f"🌍 Translating to {target_language}...")
                        progress_bar.progress(70)
                        
                        translated_segments = services['translation'].translate_segments(
                            segments, target_language
                        )
                        
                        translated_srt = services['subtitle_handler'].generate_srt(translated_segments)
                        translated_srt_path = temp_dir / "subtitles_translated.srt"
                        
                        with open(translated_srt_path, "w", encoding="utf-8") as f:
                            f.write(translated_srt)
                    
                    # Step 5: Generate video with subtitles
                    status_text.text("🎬 Adding subtitles to video...")
                    progress_bar.progress(85)
                    
                    output_path = temp_dir / f"output_{Path(video_data['filename']).stem}_with_subtitles.mp4"
                    
                    services['video_processor'].add_subtitles_to_video(
                        str(temp_video_path),
                        str(translated_srt_path),
                        str(output_path),
                        font_size=font_size,
                        font_color=font_color,
                        outline_color=outline_color
                    )
                    
                    # Step 6: Complete
                    status_text.text("✅ Processing complete!")
                    progress_bar.progress(100)
                    
                    processing_time = time.time() - start_time
                    
                    # Update database
                    if db_manager and job_id:
                        db_manager.update_job_status(job_id, 'completed')
                        db_manager.update_job_metadata(
                            job_id,
                            segments_count=len(segments),
                            processing_time=processing_time
                        )
                    
                    st.success("🎉 Video processing completed successfully!")
                    st.info(f"⏱️ Processing took {processing_time:.1f} seconds")
                    
                    # Show transcript preview
                    with st.expander("📜 View Transcript"):
                        for i, segment in enumerate(segments[:10]):
                            st.text(f"[{segment['start']:.1f}s - {segment['end']:.1f}s]: {segment['text']}")
                        if len(segments) > 10:
                            st.text(f"... and {len(segments) - 10} more segments")
                    
                    # Download buttons
                    col_a, col_b, col_c = st.columns(3)
                    
                    with col_a:
                        with open(srt_path, "r", encoding="utf-8") as f:
                            st.download_button(
                                "📄 Download Original SRT",
                                f.read(),
                                file_name="subtitles.srt",
                                mime="text/plain"
                            )
                    
                    with col_b:
                        if target_language != "none":
                            with open(translated_srt_path, "r", encoding="utf-8") as f:
                                st.download_button(
                                    "🌍 Download Translated SRT",
                                    f.read(),
                                    file_name=f"subtitles_{target_language}.srt",
                                    mime="text/plain"
                                )
                    
                    with col_c:
                        if output_path.exists():
                            with open(output_path, "rb") as f:
                                st.download_button(
                                    "🎬 Download Video with Subtitles",
                                    f.read(),
                                    file_name=output_path.name,
                                    mime="video/mp4"
                                )
                    
                    # Show processed video
                    if output_path.exists():
                        st.video(str(output_path))
                
                except Exception as e:
                    # Update job status to failed
                    if db_manager and job_id:
                        db_manager.update_job_status(job_id, 'failed', str(e))
                    
                    st.error(f"❌ Processing failed: {str(e)}")
                    st.error("🔍 Error details:")
                    st.code(traceback.format_exc())
                
                finally:
                    # Cleanup
                    try:
                        if temp_dir.exists():
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        st.warning(f"Could not clean up temporary files: {str(e)}")
        else:
            st.info("👆 Please upload a video file to start processing")
    
    # Dashboard Section
    if db_manager:
        st.markdown("---")
        st.header("📊 Processing Dashboard")
        
        try:
            stats = db_manager.get_job_statistics()
            
            col_stats1, col_stats2, col_stats3 = st.columns(3)
            
            with col_stats1:
                st.metric("Total Jobs", stats['total_jobs'])
                st.metric("Success Rate", f"{stats['success_rate']:.1f}%")
            
            with col_stats2:
                st.metric("Completed", stats['completed_jobs'])
                st.metric("Failed", stats['failed_jobs'])
            
            with col_stats3:
                avg_time = stats['average_processing_time']
                if avg_time > 0:
                    st.metric("Avg Processing Time", f"{avg_time:.1f}s")
                else:
                    st.metric("Avg Processing Time", "N/A")
            
            # Recent jobs
            with st.expander("📝 Recent Processing Jobs"):
                recent_jobs = db_manager.get_recent_jobs(limit=5)
                
                if recent_jobs:
                    for job in recent_jobs:
                        status_emoji = {
                            'completed': '✅',
                            'failed': '❌',
                            'processing': '⏳',
                            'pending': '⏱️'
                        }.get(job.status, '❓')
                        
                        col_job1, col_job2, col_job3 = st.columns([2, 1, 1])
                        
                        with col_job1:
                            st.text(f"{status_emoji} {job.filename}")
                        
                        with col_job2:
                            st.text(f"{job.status.title()}")
                        
                        with col_job3:
                            if hasattr(job, 'processing_time') and job.processing_time:
                                st.text(f"{job.processing_time:.1f}s")
                            else:
                                st.text("-")
                else:
                    st.info("No processing jobs yet")
                    
        except Exception as e:
            st.error(f"Could not load dashboard data: {str(e)}")
    
    # Footer
    st.markdown("---")
    st.markdown(
        "Powered by OpenAI Whisper for transcription and FFmpeg for video processing. "
        "Built with Streamlit."
    )

if __name__ == "__main__":
    main()