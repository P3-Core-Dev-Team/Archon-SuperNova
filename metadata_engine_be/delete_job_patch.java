    @Transactional
    public void deleteJob(Long jobId) {
        // delete cascadingly
        discoveredTableRepository.deleteByJobId(jobId);
        relationshipRepository.deleteByJobId(jobId);
        domainGroupRepository.deleteByJobId(jobId);
        sensitiveColumnRepository.deleteByJobId(jobId);
        analysisJobRepository.deleteById(jobId);
        sse.clearHistory(jobId);
    }
